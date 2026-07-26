from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from kg.embed import Embedder, cosine_similarity
from kg.ontology import Node
from kg.storage.base import StorageAdapter


@dataclass
class DedupResult:
    best_match_id: str | None
    score: float


def _get_attr(d: dict, dotted: str):
    cur: object = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def full_context_text(node: Node, embed_fields: dict[str, list[str]]) -> str:
    fields = embed_fields.get(node.type, ["name", "summary"])
    parts: list[str] = []
    for f in fields:
        if f == "name":
            parts.append(node.name or "")
        elif f == "summary":
            parts.append(node.summary or "")
        elif f.startswith("attributes."):
            val = _get_attr(node.attributes, f[len("attributes.") :])
            if val is not None:
                parts.append(str(val))
    return " ".join(p for p in parts if p)


def full_context_embedding(
    node: Node, embedder: Embedder, embed_fields: dict[str, list[str]]
) -> list[float]:
    return embedder.embed(full_context_text(node, embed_fields))


class Deduper:
    def __init__(
        self, adapter: StorageAdapter, embedder: Embedder, thresholds
    ):
        self.adapter = adapter
        self.embedder = embedder
        self.t = thresholds

    def dedup(
        self,
        node: Node,
        embed_fields: dict[str, list[str]] | None = None,
    ) -> DedupResult:
        ef = embed_fields or {}
        qemb = full_context_embedding(node, self.embedder, ef)
        qtext = full_context_text(node, ef)

        # candidates: vec top-k of same type
        cand = self.adapter.vec_search(qemb, k=10, type_filter=node.type)

        # plus same canonical_name (exact-name siblings)
        rows = self.adapter.conn.execute(
            "SELECT id, data FROM nodes WHERE status='active'"
        ).fetchall()
        cand_ids = {cid for cid, _ in cand}
        for r in rows:
            n = Node.model_validate_json(r["data"])
            if (
                n.type == node.type
                and n.id != node.id
                and n.canonical_name
                and n.canonical_name == node.canonical_name
            ):
                cand_ids.add(n.id)

        best_id, best_score = None, 0.0
        for cid in cand_ids:
            existing = self.adapter.get(cid)
            if not existing or existing.id == node.id:
                continue
            etext = full_context_text(existing, ef)
            cos = cosine_similarity(
                qemb, self.embedder.embed(etext)
            )
            fz = fuzz.token_set_ratio(qtext, etext) / 100.0
            score = (
                self.t.dedup_weights.embedding * cos
                + self.t.dedup_weights.fuzzy * fz
            )
            if score > best_score:
                best_id, best_score = existing.id, score

        return DedupResult(best_id, best_score)
