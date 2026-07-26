from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kg.ontology import Node, Edge, ALLOWED_NODE_TYPES
from kg.ontology import ALLOWED_SEMANTIC_EDGE_TYPES, STRUCTURAL_EDGE_TYPES
from kg.ids import node_id, edge_id
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

    def normalize(self, extracted_nodes, extracted_edges, source) -> SaveReport:
        report = SaveReport()
        name_to_id: dict[str, str] = {}
        seen_names: set[str] = set()

        for en in extracted_nodes:
            type_ = en["type"]
            if type_ not in ALLOWED_NODE_TYPES:
                raise ValueError(f"Unknown node type: {type_!r}")
            name = en["name"]

            if name in seen_names:
                logger.warning(
                    "Duplicate name %r in batch; skipping, first-write-wins", name)
                continue
            seen_names.add(name)

            # 2. RESOLVE (naming only)
            res = self.resolver.resolve(name, type_)
            if res.matched_id:
                node = self.adapter.get(res.matched_id)
                if name not in node.aliases and name != node.name:
                    node.aliases = [*node.aliases, name]
                    node.sources = self._add_source(node.sources, source)
                    node.updated_at = self._now()
                    self.adapter.upsert_nodes([node])
                name_to_id[name] = res.matched_id
                report.decisions.append(Decision(
                    name, type_, "RESOLVED", res.matched_id, res.score, res.via))
                continue

            # 3. EMBED + 4. DEDUP
            candidate = Node(
                id=node_id(self.user_id, type_, name),
                type=type_, subtype=en.get("subtype"),
                name=name, canonical_name=name,
                aliases=en.get("aliases", []),
                summary=en.get("summary"),
                attributes=en.get("attributes", {}),
                sources=[{"doc": source.split("#")[0],
                          "chunk": source.split("chunk-")[-1]}],
                created_at=self._now(), updated_at=self._now(),
            )
            candidate.embedding = full_context_embedding(
                candidate, self.embedder, self.config.embedding.embed_fields)
            dd = self.deduper.dedup(candidate, self.config.embedding.embed_fields)

            # 5. ROUTE
            if dd.best_match_id and dd.score >= self.config.thresholds.dedup_merge:
                # Persist candidate FIRST so merge can look it up
                self.adapter.upsert_nodes([candidate])
                self.merge(dd.best_match_id, candidate.id)
                name_to_id[name] = dd.best_match_id
                report.decisions.append(Decision(
                    name, type_, "MERGED", dd.best_match_id, dd.score, "dedup"))
            elif dd.best_match_id and dd.score >= self.config.thresholds.dedup_flag:
                self.adapter.upsert_nodes([candidate])
                self.adapter.upsert_edges([Edge(
                    id=edge_id(candidate.id, "same_as", dd.best_match_id),
                    semantic_type="same_as",
                    summary=f"gray-zone {dd.score:.2f}",
                    confidence=dd.score,
                    status="pending",
                )])
                name_to_id[name] = candidate.id
                report.new_same_as += 1
                report.decisions.append(Decision(
                    name, type_, "FLAGGED", dd.best_match_id, dd.score, "dedup"))
            else:
                self.adapter.upsert_nodes([candidate])
                name_to_id[name] = candidate.id
                report.decisions.append(Decision(
                    name, type_, "NEW", candidate.id, dd.score, "new"))

        # edges: map names to settled ids
        edges_out = []
        for ee in extracted_edges:
            sem = ee["semantic_type"]
            if sem not in (ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES):
                raise ValueError(f"Unknown edge semantic_type: {sem!r}")
            src = name_to_id.get(ee["source_name"])
            tgt = name_to_id.get(ee["target_name"])
            if not src or not tgt:
                continue
            edges_out.append(Edge(
                id=edge_id(src, sem, tgt), semantic_type=sem,
                summary=ee.get("summary"),
                sources=[{"doc": source.split("#")[0]}]))
        if edges_out:
            self.adapter.upsert_edges(edges_out)
        report.edges_upserted = len(edges_out)
        return report

    def merge(self, winner_id: str, loser_id: str) -> None:
        w = self.adapter.get(winner_id)
        l = self.adapter.get(loser_id)
        if not w or not l:
            return

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

        # re-point loser's edges to winner
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
                continue  # skip self-edge
            new_edges.append(Edge(
                id=edge_id(new_src, sem, new_tgt),
                semantic_type=old_edge.semantic_type,
                summary=old_edge.summary,
                confidence=old_edge.confidence,
                sources=old_edge.sources,
                valid_from=old_edge.valid_from,
                valid_until=old_edge.valid_until,
                status=old_edge.status,
            ))
        for oid in old_ids:
            self.adapter.conn.execute("DELETE FROM edges WHERE id=?", (oid,))
        if new_edges:
            self.adapter.upsert_edges(new_edges)
        self.adapter.conn.commit()

        # tombstone loser (never hard-delete). Embedding is preserved in the
        # serialized node for audit/recovery; adapter's status-gated vec
        # upsert + delete-then-insert keeps it out of the ANN index.
        l.status = "tombstoned"
        l.merged_into = winner_id
        self.adapter.upsert_nodes([l])

    def _add_source(self, sources, source):
        entry = {"doc": source.split("#")[0],
                 "chunk": source.split("chunk-")[-1]}
        if entry in sources:
            return sources
        return [*sources, entry]

    @staticmethod
    def _merge_unique(a, b):
        seen, out = set(), []
        for s in [*a, *b]:
            key = tuple(sorted(s.items())) if isinstance(s, dict) else s
            if key not in seen:
                seen.add(key)
                out.append(s)
        return out
