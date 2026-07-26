"""Lower the read-only Cypher AST through StorageAdapter public methods.

This module deliberately contains no database text or adapter-internal access.
Filtering/projection runs over Node objects fetched from the adapter API.
"""
from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from typing import Any

from kg.cypher.parser import (
    AST, BinaryOp, CypherError, FunctionCall, Identifier, Literal,
    NodePattern, PropertyAccess, UnaryOp,
)


def translate(ast: AST, adapter: Any) -> list[dict[str, Any]]:
    """Execute a bounded AST through adapter methods, returning projected rows.

    The adapter has no public scan operation. Therefore a MATCH needs an ID,
    a node property, or a WHERE name predicate to seed candidate lookup.
    """
    if ast.match is None or ast.return_ is None:
        raise CypherError("MATCH and RETURN are required")
    if ast.with_clauses:
        # Parsed but not lowered: silently dropping WITH would return wrong rows.
        raise CypherError("WITH is not supported by the read-only translator")
    if len(ast.match.patterns) != 1:
        raise CypherError("multiple MATCH patterns are not supported")
    pattern = ast.match.patterns[0]
    if not pattern or not isinstance(pattern[0], NodePattern):
        raise CypherError("MATCH must start with a node pattern")

    first = pattern[0]
    nodes = _seed_nodes(first, ast.where.predicate if ast.where else None, adapter)
    nodes = [n for n in nodes if _matches_node_pattern(n, first)]
    if ast.where:
        nodes = [n for n in nodes if _truth(ast.where.predicate, {first.variable: n})]

    # A relationship match expands from already-selected source nodes. This is
    # intentionally one-hop unless '*' was explicit, then uses its upper bound.
    if len(pattern) > 1:
        rel = pattern[1]
        target = pattern[2]
        depth = rel.max_hops if rel.varlen and rel.max_hops is not None else 1
        expanded = []
        for source in nodes:
            graph = adapter.neighbors(
                [source.id], depth=depth, direction=rel.direction,
                edge_types=sorted(rel.types) or None,
            )
            for node in graph.nodes:
                if _matches_node_pattern(node, target):
                    expanded.append((source, node))
        rows = [
            _project(ast, {first.variable: source, target.variable: target_node})
            for source, target_node in expanded
        ]
    else:
        rows = [_project(ast, {first.variable: node}) for node in nodes]

    if ast.return_.distinct:
        rows = _dedupe(rows)
    if ast.order_by:
        for order in reversed(ast.order_by):
            rows.sort(
                key=lambda row: _order_value(order.expr, row),
                reverse=order.descending,
            )
    return rows[:ast.limit] if ast.limit is not None else rows


def _seed_nodes(pattern: NodePattern, predicate: object | None, adapter: Any) -> list[Any]:
    """Get bounded candidates using ID lookup or full-text search."""
    node_id = _find_id_constraint(predicate, pattern.variable)
    if node_id is not None:
        node = adapter.get(node_id)
        return [node] if node is not None else []

    text = _find_text_constraint(predicate, pattern.variable)
    if text is None:
        # Exact node properties remain a useful, deterministic search seed.
        for key in ("name", "canonical_name"):
            value = pattern.properties.get(key)
            if isinstance(value, str):
                text = value
                break
    if text is None:
        raise CypherError(
            "MATCH requires a WHERE predicate on n.id or n.name "
            "(label-only scans are not supported by the read-only translator)"
        )
    # The adapter owns query interpretation. We only supply a value and fetch
    # Node records through its public API.
    hits = adapter.fts_search(text, k=200, type_filter=_one_label(pattern))
    return [node for node_id, _ in hits if (node := adapter.get(node_id)) is not None]


def _one_label(pattern: NodePattern) -> str | None:
    if len(pattern.labels) > 1:
        raise CypherError("multiple node labels are not supported")
    return next(iter(pattern.labels), None)


def _find_id_constraint(expr: object | None, variable: str | None) -> str | None:
    if not isinstance(expr, BinaryOp) or expr.op not in ("=", "=="):
        return None
    if _is_property(expr.left, variable, "id") and isinstance(expr.right, Literal):
        return str(expr.right.value)
    if _is_property(expr.right, variable, "id") and isinstance(expr.left, Literal):
        return str(expr.left.value)
    return None


def _find_text_constraint(expr: object | None, variable: str | None) -> str | None:
    if isinstance(expr, BinaryOp):
        if expr.op in ("AND", "OR"):
            return _find_text_constraint(expr.left, variable) or _find_text_constraint(expr.right, variable)
        if expr.op in ("=", "==", "=~", "CONTAINS", "STARTS_WITH", "ENDS_WITH"):
            if _is_property(expr.left, variable, "name") and isinstance(expr.right, Literal):
                return str(expr.right.value)
            if _is_property(expr.right, variable, "name") and isinstance(expr.left, Literal):
                return str(expr.left.value)
    return None


def _is_property(expr: object, variable: str | None, name: str) -> bool:
    return (
        isinstance(expr, PropertyAccess)
        and isinstance(expr.base, Identifier)
        and expr.base.name == variable
        and expr.name == name
    )


def _matches_node_pattern(node: Any, pattern: NodePattern) -> bool:
    if node is None:
        return False
    if pattern.labels and getattr(node, "type", None) not in pattern.labels:
        return False
    return all(_node_value(node, key) == value for key, value in pattern.properties.items())


def _node_value(node: Any, key: str) -> Any:
    if key == "id":
        return node.id
    if hasattr(node, key):
        return getattr(node, key)
    return (getattr(node, "attributes", None) or {}).get(key)


def _truth(expr: object, env: dict[str | None, Any]) -> bool:
    if isinstance(expr, BinaryOp):
        if expr.op == "AND":
            return _truth(expr.left, env) and _truth(expr.right, env)
        if expr.op in ("OR", "XOR"):
            left, right = _truth(expr.left, env), _truth(expr.right, env)
            return (left or right) if expr.op == "OR" else left != right
        left, right = _value(expr.left, env), _value(expr.right, env)
        if expr.op in ("=", "=="): return left == right
        if expr.op == "!=": return left != right
        if expr.op == "<": return left < right
        if expr.op == ">": return left > right
        if expr.op == "<=": return left <= right
        if expr.op == ">=": return left >= right
        if expr.op == "=~": return isinstance(left, str) and re.search(str(right), left) is not None
        if expr.op == "CONTAINS": return right in left
        if expr.op == "STARTS_WITH": return isinstance(left, str) and left.startswith(str(right))
        if expr.op == "ENDS_WITH": return isinstance(left, str) and left.endswith(str(right))
        if expr.op == "IN": return left in right
    if isinstance(expr, UnaryOp):
        value = _value(expr.operand, env)
        if expr.op == "NOT": return not bool(value)
        if expr.op == "IS_NULL": return value is None
        if expr.op == "IS_NOT_NULL": return value is not None
    return bool(_value(expr, env))


def _value(expr: object, env: dict[str | None, Any]) -> Any:
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Identifier):
        return env.get(expr.name)
    if isinstance(expr, PropertyAccess):
        return _node_value(_value(expr.base, env), expr.name)
    if isinstance(expr, FunctionCall):
        args = [_value(arg, env) for arg in expr.args]
        if expr.name.lower() == "tolower": return str(args[0]).lower()
        if expr.name.lower() == "toupper": return str(args[0]).upper()
        if expr.name.lower() == "size": return len(args[0])
        raise CypherError(f"unsupported function: {expr.name}")
    return expr


def _project(ast: AST, env: dict[str | None, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for item in ast.return_.items:
        value = _value(item.expr, env)
        key = item.alias or _expression_name(item.expr)
        if hasattr(value, "id") and hasattr(value, "name"):
            row[key] = {
                "node_id": value.id, "name": value.name,
                "type": value.type, "summary": value.summary,
            }
        else:
            row[key] = value
    # RETURN n is the common case: return its row directly, matching task API.
    if len(row) == 1 and next(iter(row.values()), None) and isinstance(next(iter(row.values())), dict):
        return next(iter(row.values()))
    return row


def _expression_name(expr: object) -> str:
    if isinstance(expr, Identifier): return expr.name
    if isinstance(expr, PropertyAccess): return expr.name
    if isinstance(expr, FunctionCall): return expr.name
    return "value"


def _order_value(expr: object, row: dict[str, Any]) -> Any:
    key = _expression_name(expr)
    return row.get(key, "")


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for row in rows:
        marker = repr(sorted(row.items()))
        if marker not in seen:
            seen.add(marker)
            out.append(row)
    return out
