from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz

from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter


@dataclass
class Candidate:
    reason: str  # "recent-pair" | "pending"
    node_ids: list[str] = field(default_factory=list)
    edge_id: str | None = None
    score: float = 0.0
    detail: str = ""
    node_type: str | None = None


def _resolve_since(since: str | None) -> str | None:
    if not since:
        return None
    try:
        # Duration like "7d", "12h", "30m"
        unit = since[-1]
        n = int(since[:-1])
        delta_map = {"d": "days", "h": "hours", "m": "minutes"}
        if unit in delta_map:
            return (datetime.now(timezone.utc) -
                    timedelta(**{delta_map[unit]: n})).isoformat()
    except (ValueError, IndexError):
        pass
    return since


def dream_candidates(
    adapter: SQLiteAdapter, *, since: str | None = None
) -> list[Candidate]:
    out: list[Candidate] = []
    since_ts = _resolve_since(since)

    # --- RECENT-PAIR: same-type active nodes ingested since `since` ---
    rows = adapter.conn.execute(
        "SELECT data FROM nodes WHERE status='active'"
    ).fetchall()
    nodes = [Node.model_validate_json(r["data"]) for r in rows]

    if since_ts:
        nodes = [n for n in nodes if (n.created_at or "") >= since_ts]

    by_type: dict[str, list[Node]] = {}
    for n in nodes:
        by_type.setdefault(n.type, []).append(n)
    for type_, group in by_type.items():
        for a, b in itertools.combinations(group, 2):
            na = a.canonical_name or a.name
            nb = b.canonical_name or b.name
            similarity = fuzz.token_set_ratio(na, nb) / 100.0
            if similarity < 0.85:
                continue
            out.append(Candidate(
                reason="recent-pair",
                node_ids=[a.id, b.id],
                node_type=type_,
                score=0.85 + (similarity * 0.1),
                detail=f"{type_} pair (canonical '{na}' vs '{nb}')",
            ))

    # --- PENDING: same_as edges with status='pending' ---
    erows = adapter.conn.execute(
        "SELECT id, source, target, data FROM edges "
        "WHERE semantic_type='same_as' AND status='pending'"
    ).fetchall()
    for r in erows:
        e = Edge.model_validate_json(r["data"])
        out.append(Candidate(
            reason="pending",
            node_ids=[r["source"], r["target"]],
            edge_id=r["id"],
            score=e.confidence,
            detail="gray-zone same_as",
        ))

    return out
