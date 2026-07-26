from __future__ import annotations

from datetime import datetime, timezone

import tiktoken

from kg.storage.base import Subgraph
from kg.traverse import degree_centrality

_ENC = tiktoken.get_encoding("cl100k_base")


def _recency(node, now: datetime | None = None) -> float:
    """Age-normalized recency; missing or malformed timestamps rank as zero."""
    value = node.updated_at or node.created_at
    if not value:
        return 0.0
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    now = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    # One-year linear decay avoids a clock-dependent absolute score explosion.
    return max(0.0, 1.0 - (now - timestamp).total_seconds() / (365 * 24 * 3600))


def _sources(node) -> str:
    return ", ".join(str(source.get("doc", "?")) for source in node.sources) or "-"


def pack(
    subgraph: Subgraph,
    seeds: dict[str, float],
    rrf_scores: dict[str, float] | None = None,
    budget_tokens: int = 4000,
) -> str:
    """Rank unique active nodes and render whole markdown blocks within budget."""
    rrf_scores = rrf_scores or {}
    centrality = degree_centrality(subgraph)
    seen: set[str] = set()
    ranked = []
    for node in subgraph.nodes:
        if node.id in seen or node.status != "active":
            continue
        seen.add(node.id)
        score = (
            0.5 * rrf_scores.get(node.id, 0.0)
            + 0.3 * centrality.get(node.id, 0.0)
            + 0.2 * _recency(node)
        )
        ranked.append((score, node))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].id or ""))

    header = "# kg context\n\n"
    parts = [header]
    used = len(_ENC.encode(header))
    for score, node in ranked:
        block = (
            f"## {node.name} ({node.type})\n"
            f"{score:.3f} · sources: {_sources(node)}\n"
            f"{node.summary or ''}\n\n"
        )
        tokens = len(_ENC.encode(block))
        if used + tokens > budget_tokens:
            continue
        parts.append(block)
        used += tokens
    return "".join(parts)
