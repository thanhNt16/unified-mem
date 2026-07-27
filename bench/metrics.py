"""Benchmark metrics: graph cleanliness, speed, cost (M6 T2).

All functions are deterministic and read-only. No LLM, no network.
``cost_from_log`` returns ``None`` until the ClaudeDriver is extended to
emit token usage (preflight: currently no usage tokens emitted).
"""
from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

__all__ = ["graph_cleanliness", "speed_metrics", "cost_from_log", "timed"]

# --- composite weights (sum to 1.0) -------------------------------------------
_W_ORPHAN = 0.25
_W_PENDING = 0.15
_W_TOMBSTONE = 0.15
_W_ATTR_CONFLICT = 0.20
_W_DUP_NAME = 0.15
_W_CONNECTIVITY = 0.10

# ponytail: fixed weights. Promote to params if user-driven weighting needed.

# Regex for suffixed duplicate node IDs (e.g. "user:person:Foo-2").
_DUP_SUFFIX_RE = re.compile(r"(.+)-(\d+)$")


def graph_cleanliness(adapter) -> dict:
    """Deterministic graph quality metrics. Reads kg.db only.

    Returns a dict with raw counts, subscores in [0, 1] (higher = cleaner),
    and a composite ``score`` in [0, 1].
    """
    rows = adapter.conn.execute(
        "SELECT id, data, status FROM nodes ORDER BY id"
    ).fetchall()

    nodes_data = []
    for r in rows:
        try:
            data = json.loads(r["data"])
        except (TypeError, ValueError):
            continue
        nodes_data.append({
            "id": r["id"],
            "status": r["status"] or data.get("status", "active"),
            "sources": data.get("sources", []) or [],
            "attribute_conflicts": data.get("attribute_conflicts", []) or [],
        })

    total = len(nodes_data)
    if total == 0:
        return {
            "total_nodes": 0,
            "orphan_nodes": 0,
            "pending_same_as": 0,
            "tombstoned_nodes": 0,
            "attr_conflict_nodes": 0,
            "duplicate_name_nodes": 0,
            "connected_nodes": 0,
            "orphan_fraction": 1.0,
            "pending_same_as_fraction": 1.0,
            "tombstone_fraction": 1.0,
            "attr_conflict_rate": 1.0,
            "duplicate_name_rate": 1.0,
            "cross_doc_connectivity": 0.0,
            "score": 0.0,
            "reason": "empty graph",
        }

    active = [n for n in nodes_data if n["status"] == "active"]
    tombstoned = [n for n in nodes_data if n["status"] == "tombstoned"]
    active_count = len(active) or 1  # guard /0

    # Orphan: active node with no sources.
    orphan_count = sum(1 for n in active if not n["sources"])

    # Pending same_as edges (status column on edges table).
    pending_rows = adapter.conn.execute(
        "SELECT COUNT(*) c FROM edges "
        "WHERE semantic_type='same_as' AND status='pending'"
    ).fetchone()
    pending_same_as = int(pending_rows["c"]) if pending_rows else 0

    # Attribute conflicts on active nodes.
    attr_conflict_nodes = sum(
        1 for n in active if n["attribute_conflicts"]
    )

    # Duplicate-name nodes: active nodes whose ID has a -N suffix.
    duplicate_name_nodes = sum(
        1 for n in active if _DUP_SUFFIX_RE.match(n["id"])
    )

    # Cross-doc connectivity: active nodes that are endpoint of >=1 active edge.
    active_ids = [n["id"] for n in active]
    connected_set: set[str] = set()
    if active_ids:
        placeholders = ",".join("?" * len(active_ids))
        conn_rows = adapter.conn.execute(
            f"SELECT DISTINCT source FROM edges WHERE status='active' "
            f"AND source IN ({placeholders})",
            active_ids,
        ).fetchall()
        for r in conn_rows:
            connected_set.add(r["source"])
        conn_rows = adapter.conn.execute(
            f"SELECT DISTINCT target FROM edges WHERE status='active' "
            f"AND target IN ({placeholders})",
            active_ids,
        ).fetchall()
        for r in conn_rows:
            connected_set.add(r["target"])

    # Subscores (1.0 = perfectly clean; 0.0 = maximally dirty).
    orphan_fraction_clean = 1.0 - (orphan_count / active_count)
    pending_same_as_fraction_clean = 1.0 - min(
        1.0, pending_same_as / active_count
    )
    tombstone_fraction_clean = 1.0 - (len(tombstoned) / total)
    attr_conflict_rate_clean = 1.0 - (attr_conflict_nodes / active_count)
    duplicate_name_rate_clean = 1.0 - (duplicate_name_nodes / active_count)
    cross_doc_connectivity = len(connected_set) / active_count

    score = (
        _W_ORPHAN * orphan_fraction_clean
        + _W_PENDING * pending_same_as_fraction_clean
        + _W_TOMBSTONE * tombstone_fraction_clean
        + _W_ATTR_CONFLICT * attr_conflict_rate_clean
        + _W_DUP_NAME * duplicate_name_rate_clean
        + _W_CONNECTIVITY * cross_doc_connectivity
    )

    return {
        "total_nodes": total,
        "orphan_nodes": orphan_count,
        "pending_same_as": pending_same_as,
        "tombstoned_nodes": len(tombstoned),
        "attr_conflict_nodes": attr_conflict_nodes,
        "duplicate_name_nodes": duplicate_name_nodes,
        "connected_nodes": len(connected_set),
        # Raw rates (0 = clean, 1 = dirty):
        "orphan_fraction": orphan_count / active_count,
        "pending_same_as_fraction": min(1.0, pending_same_as / active_count),
        "tombstone_fraction": len(tombstoned) / total,
        "attr_conflict_rate": attr_conflict_nodes / active_count,
        "duplicate_name_rate": duplicate_name_nodes / active_count,
        # Subscores (1 = clean, 0 = dirty):
        "orphan_subscore": orphan_fraction_clean,
        "pending_same_as_subscore": pending_same_as_fraction_clean,
        "tombstone_subscore": tombstone_fraction_clean,
        "attr_conflict_subscore": attr_conflict_rate_clean,
        "duplicate_name_subscore": duplicate_name_rate_clean,
        "cross_doc_connectivity": cross_doc_connectivity,
        "score": score,
    }


@contextmanager
def timed():
    """Context manager yielding elapsed wall-clock seconds.

    Usage::

        with timed() as t:
            do_work()
        print(t.elapsed)
    """
    start = time.perf_counter()
    holder = type("Holder", (), {"elapsed": 0.0})()
    try:
        yield holder
    finally:
        holder.elapsed = time.perf_counter() - start


def speed_metrics(runner_fn, *, n: int = 5) -> dict:
    """Time ``runner_fn`` N times. Return median/p95/min/max in seconds.

    Pure timing harness. ``runner_fn`` must be a no-arg callable. We do not
    warm up — the first run is included; determinism is best-effort for
    deterministic operations.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    samples: list[float] = []
    for _ in range(n):
        with timed() as t:
            runner_fn()
        samples.append(t.elapsed)
    samples.sort()
    # Nearest-rank percentile (deterministic, no interpolation).
    p95_idx = max(0, min(len(samples) - 1, int(round(0.95 * (len(samples) - 1)))))
    return {
        "n": n,
        "min_seconds": samples[0],
        "max_seconds": samples[-1],
        "median_seconds": samples[len(samples) // 2],
        "p95_seconds": samples[p95_idx],
        "samples_seconds": list(samples),
    }


# --- cost --------------------------------------------------------------------

_NO_TOKEN_REASON = (
    "ClaudeDriver parses stream-json but emits assistant text only; "
    "no usage tokens available. cost_from_log returns None until the "
    "driver is extended to capture message.usage. Do not fabricate."
)


def cost_from_log(paths) -> dict | None:
    """Parse harness token output from one or more log paths.

    Returns ``None`` when no usage tokens are found — per M5 preflight,
    ClaudeDriver emits assistant text only, no usage block. When token
    data exists (future), parse it as JSON lines and aggregate tokens.

    Args:
        paths: a path or iterable of paths (str or Path) to inspect.

    Returns:
        None if no token data, else a dict with keys:
            - input_tokens, output_tokens, total_tokens (int)
            - usd (estimated cost; 0.0 if pricing unknown)
            - lines_parsed (int)
            - sources (list[str])
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = [Path(p) for p in paths]

    total_input = 0
    total_output = 0
    lines_with_usage = 0
    sources_used: list[str] = []

    for p in paths:
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            usage = obj.get("usage") or obj.get("message", {}).get("usage")
            if not isinstance(usage, dict):
                continue
            inp = usage.get("input_tokens") or usage.get("inputTokens") or 0
            out = usage.get("output_tokens") or usage.get("outputTokens") or 0
            if not inp and not out:
                continue
            try:
                total_input += int(inp)
                total_output += int(out)
                lines_with_usage += 1
                if str(p) not in sources_used:
                    sources_used.append(str(p))
            except (TypeError, ValueError):
                continue

    if lines_with_usage == 0:
        return None

    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "usd": 0.0,  # pricing depends on model; leave 0 until price table wired
        "lines_parsed": lines_with_usage,
        "sources": sources_used,
        "reason": None,
    }


def cost_null_reason() -> str:
    """Return the documented null contract reason for cost_from_log."""
    return _NO_TOKEN_REASON
