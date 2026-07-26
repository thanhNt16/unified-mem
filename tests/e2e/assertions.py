"""Graph-shape and tool-JSON assertions for E2E distribution tests.

assert_graph_shape:
    Open a kg.db via SQLiteAdapter, count nodes/edges, check ontology
    conformance, and report active vs tombstone counts. Counts must fall
    inside caller-supplied [min, max] bands.

kg_tool_json_equal:
    Structural equality for kg MCP/CLI tool JSON responses. Ignores unstable
    fields (timestamps, embedding vectors), compares node-id sets, edge-id
    sets, and expansion id sets. Order-independent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kg.ontology import (
    ALLOWED_NODE_TYPES,
    ALLOWED_SEMANTIC_EDGE_TYPES,
    STRUCTURAL_EDGE_TYPES,
)
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter


# Fields that are timestamp/unstable/order-dependent and must NOT participate
# in kg_tool_json_equal. Add sparingly — every field here is one fewer signal
# for catching real regressions.
_UNSTABLE_TOOL_FIELDS = frozenset({
    "timestamp", "created_at", "updated_at", "valid_from", "valid_until",
    "embedding", "summary",  # summaries are LLM paraphrases; id+type are stable
    "score", "distance",  # ranking floats drift across embedding versions
    "confidence",
})

_VALID_EDGE_TYPES = ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES


@dataclass
class GraphShape:
    """Structured result of assert_graph_shape. Returned for inspection."""
    node_count: int
    edge_count: int
    active_nodes: int
    tombstoned_nodes: int
    active_edges: int
    node_types: dict[str, int] = field(default_factory=dict)
    edge_types: dict[str, int] = field(default_factory=dict)
    invalid_node_ids: list[str] = field(default_factory=list)
    invalid_edge_ids: list[str] = field(default_factory=list)


class ToolJsonMismatch(AssertionError):
    """Raised by kg_tool_json_equal when structural comparison fails."""


def assert_graph_shape(
    project_root: Path,
    *,
    nodes: tuple[int | None, int | None] = (None, None),
    edges: tuple[int | None, int | None] = (None, None),
) -> GraphShape:
    """Assert kg.db at project_root/.kg/kg.db has shape inside given bands.

    Bands are inclusive: nodes=(2, 5) means 2 <= count <= 5. None means
    unbounded on that side (default).

    Checks performed (each raises AssertionError on failure):
        1. Total node count inside `nodes` band.
        2. Total edge count inside `edges` band.
        3. Every node.type ∈ ALLOWED_NODE_TYPES.
        4. Every edge.semantic_type ∈ ALLOWED_SEMANTIC_EDGE_TYPES ∪
           STRUCTURAL_EDGE_TYPES.
        5. Tombstoned vs active counts (returned; not asserted — caller can
           inspect if needed).

    Returns a GraphShape describing what was found. Never returns on failure;
    raises AssertionError with a precise message instead.

    Args:
        project_root: directory containing `.kg/kg.db`.
        nodes: (min, max) inclusive band for node count; None = unbounded.
        edges: (min, max) inclusive band for edge count; None = unbounded.
    """
    kg_db = KgPaths.for_root(Path(project_root) / ".kg").kg_db
    if not kg_db.exists():
        raise AssertionError(f"no kg.db at {kg_db}")
    adapter = SQLiteAdapter(kg_db)

    n_lo, n_hi = nodes
    e_lo, e_hi = edges

    # Walk every stored node (active + tombstoned) for type conformance.
    node_types: dict[str, int] = {}
    invalid_node_ids: list[str] = []
    active_nodes = 0
    tombstoned_nodes = 0
    row = adapter.conn.execute("SELECT id, data, status FROM nodes").fetchall()
    for r in row:
        data = json.loads(r["data"])
        nt = data.get("type", "")
        node_types[nt] = node_types.get(nt, 0) + 1
        if nt not in ALLOWED_NODE_TYPES:
            invalid_node_ids.append(r["id"])
        if r["status"] == "active":
            active_nodes += 1
        elif r["status"] == "tombstoned":
            tombstoned_nodes += 1

    edge_types: dict[str, int] = {}
    invalid_edge_ids: list[str] = []
    active_edges = 0
    erows = adapter.conn.execute("SELECT id, semantic_type, status FROM edges").fetchall()
    for r in erows:
        st = r["semantic_type"] or ""
        edge_types[st] = edge_types.get(st, 0) + 1
        if st not in _VALID_EDGE_TYPES:
            invalid_edge_ids.append(r["id"])
        if r["status"] == "active":
            active_edges += 1

    total_nodes = len(row)
    total_edges = len(erows)

    shape = GraphShape(
        node_count=total_nodes,
        edge_count=total_edges,
        active_nodes=active_nodes,
        tombstoned_nodes=tombstoned_nodes,
        active_edges=active_edges,
        node_types=node_types,
        edge_types=edge_types,
        invalid_node_ids=invalid_node_ids,
        invalid_edge_ids=invalid_edge_ids,
    )

    _assert_band("nodes", total_nodes, n_lo, n_hi)
    _assert_band("edges", total_edges, e_lo, e_hi)
    if invalid_node_ids:
        raise AssertionError(
            f"nodes with disallowed type: {invalid_node_ids} "
            f"(types seen={sorted(node_types)}, allowed={sorted(ALLOWED_NODE_TYPES)})"
        )
    if invalid_edge_ids:
        raise AssertionError(
            f"edges with disallowed semantic_type: {invalid_edge_ids} "
            f"(types seen={sorted(edge_types)}, allowed={sorted(_VALID_EDGE_TYPES)})"
        )

    return shape


def _assert_band(label: str, n: int, lo: int | None, hi: int | None) -> None:
    if lo is not None and n < lo:
        raise AssertionError(f"{label} count {n} < min {lo}")
    if hi is not None and n > hi:
        raise AssertionError(f"{label} count {n} > max {hi}")


def kg_tool_json_equal(a: Any, b: Any, *, path: str = "") -> bool:
    """Order-independent structural equality for kg MCP/CLI tool JSON.

    Comparison rules:
        - dicts: same keys (ignoring unstable fields), recurse on each value.
        - lists of dicts with stable ids (node-id, edge-id, id, source+target):
          compared as sets keyed by the stable id, ignoring order.
        - lists without stable ids: sorted then compared (falls back to
          order-insensitive comparison for primitives).
        - primitives: direct ==.

    Raises ToolJsonMismatch with a dotted path on first difference.
    Returns True when structurally equal.
    """
    a = _coerce(a)
    b = _coerce(b)
    _compare(a, b, path or "$")
    return True


def _coerce(x: Any) -> Any:
    if isinstance(x, (str, bytes)) and x:
        try:
            return json.loads(x)
        except (ValueError, TypeError):
            return x
    return x


def _stable_id(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for k in ("id", "node_id", "node-id", "edge_id", "edge-id"):
        if k in item:
            return f"{k}={item[k]}"
    if "source" in item and "target" in item:
        return f"src={item['source']}|tgt={item['target']}"
    return None


def _compare(a: Any, b: Any, path: str) -> None:
    if type(a) is not type(b):
        # Allow int/float numeric equivalence (e.g. 1 vs 1.0).
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b:
            return
        raise ToolJsonMismatch(f"{path}: type mismatch {type(a).__name__} vs {type(b).__name__}")

    if isinstance(a, dict):
        a_keys = {k for k in a if k not in _UNSTABLE_TOOL_FIELDS}
        b_keys = {k for k in b if k not in _UNSTABLE_TOOL_FIELDS}
        if a_keys != b_keys:
            missing = a_keys - b_keys
            extra = b_keys - a_keys
            raise ToolJsonMismatch(
                f"{path}: key mismatch keys-only-in-a={sorted(missing)} "
                f"keys-only-in-b={sorted(extra)} (unstable fields ignored: "
                f"{sorted(set(a) & _UNSTABLE_TOOL_FIELDS)})"
            )
        for k in a_keys:
            _compare(a[k], b[k], f"{path}.{k}")
        return

    if isinstance(a, list):
        # If every element has a stable id, compare as a set — order-
        # insensitive AND size-insensitive (different counts surface as
        # only-in-a / only-in-b rather than as a coarse length mismatch).
        a_ids = [_stable_id(x) for x in a]
        b_ids = [_stable_id(x) for x in b]
        if a and b and all(a_ids) and all(b_ids):
            by_a = {_stable_id(x): x for x in a}
            by_b = {_stable_id(x): x for x in b}
            common = set(by_a) & set(by_b)
            for k in common:
                _compare(by_a[k], by_b[k], f"{path}[{k}]")
            only_a = sorted(set(by_a) - set(by_b))
            only_b = sorted(set(by_b) - set(by_a))
            if only_a or only_b:
                raise ToolJsonMismatch(
                    f"{path}: id-set mismatch only-in-a={only_a} only-in-b={only_b}"
                )
            return

        # No stable ids: compare as a multiset by value (sorted).
        if len(a) != len(b):
            raise ToolJsonMismatch(f"{path}: list length {len(a)} vs {len(b)}")
        try:
            for i, (x, y) in enumerate(zip(sorted(a, key=json.dumps),
                                            sorted(b, key=json.dumps))):
                _compare(x, y, f"{path}[{i}]")
        except TypeError:
            for i, x in enumerate(a):
                if not any(_equal(x, y) for y in b):
                    raise ToolJsonMismatch(
                        f"{path}[{i}]: no match in other list"
                    )
        return

    # primitives
    if a != b:
        raise ToolJsonMismatch(f"{path}: {a!r} != {b!r}")


def _equal(a: Any, b: Any) -> bool:
    try:
        _compare(a, b, "$")
        return True
    except ToolJsonMismatch:
        return False
