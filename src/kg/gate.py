from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kg.ontology import Node, Edge, ALLOWED_NODE_TYPES
from kg.ontology import ALLOWED_SEMANTIC_EDGE_TYPES, STRUCTURAL_EDGE_TYPES
from kg.ids import allocate_node_id, edge_id, node_id
from kg.dedup import full_context_embedding

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    name: str
    type: str
    action: str  # NEW | RESOLVED | MERGED | FLAGGED
    target_id: str | None
    score: float
    via: str


@dataclass
class SaveReport:
    decisions: list[Decision] = field(default_factory=list)
    edges_upserted: int = 0
    new_same_as: int = 0
    dropped_edges: list[dict] = field(default_factory=list)


@dataclass
class AuditRecord:
    timestamp: str
    action: str
    winner_id: str | None
    loser_id: str | None
    review_edge_id: str | None
    winner_before: dict | None
    loser_before: dict | None
    edges_before: list[dict]


class Gate:
    def __init__(self, adapter, resolver, deduper, embedder, config, user_id):
        self.adapter = adapter
        self.resolver = resolver
        self.deduper = deduper
        self.embedder = embedder
        self.config = config
        self.user_id = user_id

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def normalize(
        self, extracted_nodes, extracted_edges, source, facts=None, preferences=None
    ) -> SaveReport:
        facts = facts or []
        preferences = preferences or []
        node_stream = [*(dict(n) for n in extracted_nodes)]
        node_stream.extend({
            "type": "fact",
            "name": str(f["subject"]),
            "summary": f.get("summary"),
            "attributes": {
                "subject": f["subject"],
                "predicate": f["predicate"],
                "object": f["object"],
            },
            "_skip_resolver": True,
        } for f in facts)
        for p in preferences:
            pref = dict(p)
            name = pref.get("name") or pref.get("subject")
            if not name:
                raise ValueError("Preference requires name or subject")
            node_stream.append({
                "type": "preference",
                "name": str(name),
                "summary": pref.get("summary"),
                "attributes": pref.get("attributes", pref),
                "valid_from": pref.get("valid_from"),
                "valid_until": pref.get("valid_until"),
                "_skip_resolver": True,
            })

        for en in node_stream:
            if en["type"] not in ALLOWED_NODE_TYPES:
                raise ValueError(f"Unknown node type: {en['type']!r}")
        for ee in extracted_edges:
            if ee["semantic_type"] not in (
                ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES
            ):
                raise ValueError(
                    f"Unknown edge semantic_type: {ee['semantic_type']!r}")

        with self.adapter.transaction():
            return self._normalize(node_stream, extracted_edges, source)

    def _normalize(self, node_stream, extracted_edges, source) -> SaveReport:
        report = SaveReport()
        name_to_ids: dict[str, list[str]] = {}
        seen_keys: set[tuple[str, str]] = set()
        embed_fields = dict(self.config.embedding.embed_fields)
        embed_fields.setdefault(
            "fact",
            ["name", "summary", "attributes.subject", "attributes.predicate",
             "attributes.object"],
        )

        for en in node_stream:
            type_ = en["type"]
            name = en["name"]
            key = (type_, name)
            if key in seen_keys:
                logger.warning(
                    "Duplicate (type, name) %r in batch; skipping, first-write-wins", key)
                continue
            seen_keys.add(key)

            # RESOLVE is a naming hint only. Identity is always decided by DEDUP.
            res = None
            if not en.get("_skip_resolver"):
                res = self.resolver.resolve(name, type_)

            candidate = Node(
                id=None,
                type=type_, subtype=en.get("subtype"),
                name=name,
                canonical_name=None if en.get("_skip_resolver") else name,
                aliases=en.get("aliases", []),
                summary=en.get("summary"),
                attributes=en.get("attributes", {}),
                valid_from=en.get("valid_from"),
                valid_until=en.get("valid_until"),
                sources=[self._source_entry(source)],
                created_at=self._now(), updated_at=self._now(),
            )
            candidate.embedding = full_context_embedding(
                candidate, self.embedder, embed_fields)
            dd = self.deduper.dedup(candidate, embed_fields)
            base_id = node_id(self.user_id, type_, name)

            if dd.best_match_id and dd.score >= self.config.thresholds.dedup_merge:
                self._merge_candidate(dd.best_match_id, candidate, res)
                settled_id = dd.best_match_id
                report.decisions.append(Decision(
                    name, type_, "MERGED", settled_id, dd.score, "dedup"))
            elif dd.best_match_id and dd.score >= self.config.thresholds.dedup_flag:
                candidate.id = allocate_node_id(
                    self.adapter, self.user_id, type_, name)
                self.adapter.upsert_nodes([candidate])
                self.adapter.upsert_edges([Edge(
                    id=edge_id(candidate.id, "same_as", dd.best_match_id),
                    semantic_type="same_as",
                    summary=f"gray-zone {dd.score:.2f}",
                    confidence=dd.score,
                    status="pending",
                )])
                settled_id = candidate.id
                report.new_same_as += 1
                report.decisions.append(Decision(
                    name, type_, "FLAGGED", dd.best_match_id, dd.score, "dedup"))
            else:
                candidate.id = allocate_node_id(
                    self.adapter, self.user_id, type_, name)
                self.adapter.upsert_nodes([candidate])
                settled_id = candidate.id
                report.decisions.append(Decision(
                    name, type_, "NEW", settled_id, dd.score, "new"))
            name_to_ids.setdefault(name, []).append(settled_id)

        edges_out = []
        for ee in extracted_edges:
            sem = ee["semantic_type"]
            source_ids = name_to_ids.get(ee["source_name"], [])
            target_ids = name_to_ids.get(ee["target_name"], [])
            if len(source_ids) != 1 or len(target_ids) != 1:
                reason = (
                    "ambiguous" if len(source_ids) > 1 or len(target_ids) > 1
                    else "missing"
                )
                logger.warning(
                    "Ambiguous or missing edge endpoint(s) %r -> %r; skipping",
                    ee["source_name"], ee["target_name"])
                report.dropped_edges.append({
                    "source_name": ee["source_name"],
                    "target_name": ee["target_name"],
                    "semantic_type": sem,
                    "reason": reason,
                })
                continue
            src, tgt = source_ids[0], target_ids[0]
            edges_out.append(Edge(
                id=edge_id(src, sem, tgt), semantic_type=sem,
                summary=ee.get("summary"),
                confidence=ee.get("confidence", 0.0),
                sources=[{"doc": source.split("#")[0]}]))
        if edges_out:
            self.adapter.upsert_edges(edges_out)
        report.edges_upserted = len(edges_out)
        return report

    def merge(self, winner_id: str, loser_id: str) -> None:
        with self.adapter.transaction():
            w = self.adapter.get(winner_id)
            l = self.adapter.get(loser_id)
            if not w or not l:
                raise ValueError(f"unknown node(s): {winner_id}, {loser_id}")
            if winner_id == loser_id:
                raise ValueError("cannot merge a node into itself")
            if w.status != "active" or l.status != "active":
                raise ValueError("both endpoints must be active")
            if w.type != l.type:
                raise ValueError(f"type mismatch {w.type} != {l.type}")
            self._merge_inplace(w, l, winner_id, loser_id)

    def _merge_inplace(self, w, l, winner_id, loser_id) -> None:
        w.aliases = list(dict.fromkeys([*w.aliases, l.name, *l.aliases]))
        w.sources = self._merge_unique(w.sources, l.sources)
        for k, v in l.attributes.items():
            if k in w.attributes and w.attributes[k] != v:
                w.attribute_conflicts.append(
                    {"key": k, "winner": w.attributes[k], "loser": v})
            else:
                w.attributes[k] = v
        if l.summary and (not w.summary or len(l.summary) > len(w.summary)):
            w.summary = l.summary
        w.embedding = full_context_embedding(
            w, self.embedder, self.config.embedding.embed_fields)
        w.updated_at = self._now()
        self.adapter.upsert_nodes([w])

        rows = self.adapter.conn.execute(
            "SELECT id, data FROM edges WHERE source=? OR target=?",
            (loser_id, loser_id),
        ).fetchall()
        old_ids: list[str] = []
        new_edges: list[Edge] = []
        for r in rows:
            old_id = r["id"]
            old_edge = Edge.model_validate_json(r["data"])
            src, sem, tgt = old_id.split("|", 2)
            new_src = winner_id if src == loser_id else src
            new_tgt = winner_id if tgt == loser_id else tgt
            old_ids.append(old_id)
            if new_src == new_tgt:
                continue
            rebuilt = Edge(
                id=edge_id(new_src, sem, new_tgt),
                semantic_type=old_edge.semantic_type,
                summary=old_edge.summary,
                confidence=old_edge.confidence,
                sources=old_edge.sources,
                valid_from=old_edge.valid_from,
                valid_until=old_edge.valid_until,
                status=old_edge.status,
            )
            existing_row = self.adapter.conn.execute(
                "SELECT data FROM edges WHERE id=?", (rebuilt.id,)
            ).fetchone()
            if existing_row:
                existing = Edge.model_validate_json(existing_row["data"])
                rebuilt.sources = self._merge_unique(
                    existing.sources, rebuilt.sources)
                if existing.summary and (
                    not rebuilt.summary
                    or len(existing.summary) >= len(rebuilt.summary)
                ):
                    rebuilt.summary = existing.summary
                rebuilt.confidence = max(existing.confidence, rebuilt.confidence)
                rebuilt.valid_from = existing.valid_from or rebuilt.valid_from
                rebuilt.valid_until = existing.valid_until or rebuilt.valid_until
                rebuilt.status = existing.status
            new_edges.append(rebuilt)
        for old_id in old_ids:
            self.adapter.conn.execute("DELETE FROM edges WHERE id=?", (old_id,))
        if new_edges:
            self.adapter.upsert_edges(new_edges)

        l.status = "tombstoned"
        l.merged_into = winner_id
        self.adapter.upsert_nodes([l])

    def _load_review_edge(self, edge_id: str) -> Edge:
        row = self.adapter.conn.execute(
            "SELECT data FROM edges WHERE id=?", (edge_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown edge {edge_id}")
        e = Edge.model_validate_json(row["data"])
        if e.semantic_type != "same_as":
            raise ValueError("edge is not same_as")
        # Check both relational column and serialized payload.
        row2 = self.adapter.conn.execute(
            "SELECT status FROM edges WHERE id=?", (edge_id,)
        ).fetchone()
        if row2["status"] != "pending" or e.status != "pending":
            raise ValueError(f"edge {edge_id} not pending")
        return e

    def review_merge(
        self, edge_id: str, winner_id: str, loser_id: str
    ) -> AuditRecord:
        with self.adapter.transaction():
            e = self._load_review_edge(edge_id)
            src, _, tgt = edge_id.split("|", 2)
            endpoints = {src, tgt}
            if {winner_id, loser_id} != endpoints:
                raise ValueError("winner/loser must be the edge endpoints")
            if winner_id == loser_id:
                raise ValueError("cannot merge a node into itself")
            w = self.adapter.get(winner_id)
            l = self.adapter.get(loser_id)
            if not w or not l:
                raise ValueError(f"unknown node(s): {winner_id}, {loser_id}")
            if w.status != "active" or l.status != "active":
                raise ValueError("both endpoints must be active")
            if w.type != l.type:
                raise ValueError(f"type mismatch {w.type} != {l.type}")

            edges_before = [
                Edge.model_validate_json(r["data"]).model_dump(mode="json")
                for r in self.adapter.conn.execute(
                    "SELECT data FROM edges WHERE source=? OR target=?",
                    (loser_id, loser_id),
                ).fetchall()
            ]
            winner_before = w.model_dump(mode="json")
            loser_before = l.model_dump(mode="json")

            self._merge_inplace(w, l, winner_id, loser_id)
            self.adapter.conn.execute("DELETE FROM edges WHERE id=?", (edge_id,))

            return AuditRecord(
                timestamp=self._now(),
                action="review_confirm",
                winner_id=winner_id, loser_id=loser_id,
                review_edge_id=edge_id,
                winner_before=winner_before, loser_before=loser_before,
                edges_before=edges_before,
            )

    def reject_review(self, edge_id: str) -> AuditRecord:
        with self.adapter.transaction():
            e = self._load_review_edge(edge_id)
            src, _, tgt = edge_id.split("|", 2)
            edges_before = [e.model_dump(mode="json")]
            e.status = "rejected"
            self.adapter.upsert_edges([e])
            return AuditRecord(
                timestamp=self._now(),
                action="review_reject",
                winner_id=None, loser_id=None, review_edge_id=edge_id,
                winner_before=None, loser_before=None,
                edges_before=edges_before,
            )

    def _merge_candidate(self, winner_id, candidate, resolution) -> None:
        winner = self.adapter.get(winner_id)
        if not winner:
            return
        self._enrich_from_resolution(candidate, resolution)
        winner.aliases = list(dict.fromkeys([
            *winner.aliases, *candidate.aliases,
            *([candidate.name] if candidate.name != winner.name else []),
        ]))
        winner.sources = self._merge_unique(winner.sources, candidate.sources)
        for key, value in candidate.attributes.items():
            if key not in winner.attributes:
                winner.attributes[key] = value
        if candidate.summary and (
            not winner.summary or len(candidate.summary) > len(winner.summary)
        ):
            winner.summary = candidate.summary
        winner.embedding = full_context_embedding(
            winner, self.embedder, self.config.embedding.embed_fields)
        winner.updated_at = self._now()
        self.adapter.upsert_nodes([winner])

    def _enrich_from_resolution(self, candidate, resolution) -> None:
        if not resolution or not resolution.matched_id:
            return
        named = self.adapter.get(resolution.matched_id)
        if not named:
            return
        candidate.aliases = list(dict.fromkeys([
            *candidate.aliases, *named.aliases,
            *([named.name] if named.name != candidate.name else []),
        ]))

    @staticmethod
    def _source_entry(source):
        chunk = source.split("chunk-", 1)[1] if "chunk-" in source else "0"
        return {"doc": source.split("#")[0], "chunk": chunk}

    def _add_source(self, sources, source):
        entry = self._source_entry(source)
        if entry in sources:
            return sources
        return [*sources, entry]

    @staticmethod
    def _merge_unique(a, b):
        seen, out = set(), []
        for item in [*a, *b]:
            key = tuple(sorted(item.items())) if isinstance(item, dict) else item
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out
