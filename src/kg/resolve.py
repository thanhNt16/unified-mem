from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from kg.embed import Embedder
from kg.ontology import Node
from kg.storage.base import StorageAdapter


@dataclass
class Resolution:
    matched_id: str | None
    canonical_name: str
    via: str  # exact | fuzzy | semantic | none
    score: float


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


class Resolver:
    def __init__(
        self,
        adapter: StorageAdapter,
        embedder: Embedder,
        thresholds,
        user_id: str,
    ):
        self.adapter = adapter
        self.embedder = embedder
        self.t = thresholds
        self.user_id = user_id

    def _candidates(self, type_: str) -> list[Node]:
        rows = self.adapter.conn.execute(
            "SELECT data FROM nodes WHERE status='active'"
        ).fetchall()
        out: list[Node] = []
        for r in rows:
            n = Node.model_validate_json(r["data"])
            if n.type == type_:
                out.append(n)
        return out

    def resolve(self, name: str, type_: str) -> Resolution:
        cands = self._candidates(type_)
        target = _norm(name)

        # 1. exact (alias or name)
        for n in cands:
            names = {_norm(n.name), *(_norm(a) for a in n.aliases)}
            if target in names:
                return Resolution(n.id, n.canonical_name or n.name, "exact", 1.0)

        # 2. fuzzy
        best_id, best_score = None, 0.0
        for n in cands:
            sc = max(
                fuzz.token_set_ratio(target, _norm(n.name)) / 100.0,
                *(
                    fuzz.token_set_ratio(target, _norm(a)) / 100.0
                    for a in n.aliases
                ),
            )
            if sc > best_score:
                best_id, best_score = n.id, sc
        if best_id and best_score >= self.t.resolve_fuzzy:
            node = self.adapter.get(best_id)
            return Resolution(
                best_id, node.canonical_name or node.name, "fuzzy", best_score
            )

        # 3. semantic (name-only embedding)
        qv = self.embedder.embed(name)
        best_id, best_score = None, 0.0
        for n in cands:
            nv = self.embedder.embed(n.name)
            cos = sum(a * b for a, b in zip(qv, nv))
            if cos > best_score:
                best_id, best_score = n.id, cos
        if best_id and best_score >= self.t.resolve_semantic:
            node = self.adapter.get(best_id)
            return Resolution(
                best_id, node.canonical_name or node.name, "semantic", best_score
            )

        return Resolution(None, name, "none", 0.0)
