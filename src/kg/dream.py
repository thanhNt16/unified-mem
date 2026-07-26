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


def _resolve_since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        # Duration like "7d", "12h", "30m"
        unit = since[-1]
        n = int(since[:-1])
        delta_map = {"d": "days", "h": "hours", "m": "minutes"}
        if unit in delta_map:
            return datetime.now(timezone.utc) - timedelta(**{delta_map[unit]: n})
    except (ValueError, IndexError):
        pass
    # Otherwise treat as ISO-8601 timestamp cutoff.
    return _parse_ts(since)


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # fromisoformat handles date-only and Z-suffixed ISO-8601 values.
        value = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    except ValueError:
        return None


def _node_recent(n: Node, cutoff: datetime) -> bool:
    """Node is in-window if EITHER created_at or updated_at >= cutoff."""
    c = _parse_ts(n.created_at)
    u = _parse_ts(n.updated_at)
    latest = max(filter(None, (c, u)), default=None)
    return latest is not None and latest >= cutoff


def dream_candidates(
    adapter: SQLiteAdapter, *, since: str | None = None
) -> list[Candidate]:
    out: list[Candidate] = []
    since_ts = _resolve_since(since)

    # All sweeps deliberately query active nodes only.
    rows = adapter.conn.execute(
        "SELECT data FROM nodes WHERE status='active'"
    ).fetchall()
    nodes = sorted((Node.model_validate_json(r["data"]) for r in rows), key=lambda n: n.id or "")
    recent_nodes = nodes
    if since_ts:
        recent_nodes = [n for n in nodes if _node_recent(n, since_ts)]

    # --- RECENT-PAIR: same-type active nodes ingested since `since` ---
    by_type: dict[str, list[Node]] = {}
    for n in recent_nodes:
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

    # --- EXPIRING: active facts/preferences whose validity already ended ---
    now = datetime.now(timezone.utc)
    for n in nodes:
        valid_until = _parse_ts(n.valid_until)
        if n.type in {"fact", "preference"} and valid_until and valid_until < now:
            out.append(Candidate(
                reason="expiring",
                node_ids=[n.id],
                detail=f"valid_until {n.valid_until}",
            ))

    # --- ORPHAN: narrow, supported definition — no recorded sources ---
    for n in nodes:
        if not n.sources:
            out.append(Candidate(reason="orphan", node_ids=[n.id], detail="orphan"))

    # --- CONTRADICT: same fact subject/predicate (name), different object (summary) ---
    facts = [n for n in nodes if n.type == "fact"]
    for a, b in itertools.combinations(facts, 2):
        if a.name == b.name and (a.summary or "") != (b.summary or ""):
            out.append(Candidate(
                reason="contradict",
                node_ids=[a.id, b.id],
                detail=f"name={a.name}",
            ))

    return out
