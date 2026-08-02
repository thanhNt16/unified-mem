"""Normalize kg graph data and serialize the CBM API contract.

Consumes ``StorageAdapter`` (active-row SQL) and ``layout_graph`` from
``kg.viz.layout3d``. Deliberately does NOT consume ``louvain_cached``: the
pinned CBM payload has no cluster field, and ``cluster_key`` derives from
``attributes.path`` or ``type``, so community detection never runs on this
path (deviation from the plan's interface list — see task report).
"""
from __future__ import annotations

import json
from pathlib import Path

from kg.storage.base import StorageAdapter
from kg.viz.layout3d import LayoutEdgeInput, LayoutNodeInput, layout_graph

_MAX_NODES = 2_000
_MAX_EDGES = 4_000
_MAX_BYTES = 4 * 1024 * 1024  # 4 MiB

# Fixed accessible color map keyed by existing kg node type.
_COLOR_BY_TYPE = {
    "person": "#3b82f6",
    "organization": "#ef4444",
    "location": "#10b981",
    "event": "#f59e0b",
    "object": "#8b5cf6",
    "preference": "#ec4899",
    "fact": "#0ea5e9",
    "document": "#6b7280",
    "chunk": "#9ca3af",
    "conversation": "#22c55e",
    "session": "#a78bfa",
}
_DEFAULT_COLOR = "#475569"


class PayloadTooLarge(ValueError):
    """Raised when a serialized layout payload exceeds ``_MAX_BYTES``."""


def json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def build_capabilities(*, static: bool) -> dict[str, bool]:
    return {
        "graph": True,
        "projects": not static,
        "control": not static,
        "index": not static,
        "code_view": False,
        "adr": False,
        "dead_code": False,
        "missed_graph": False,
    }


def _cluster_key(data: dict) -> str:
    """Path-derived cluster key: first three path components, else node type."""
    attrs = data.get("attributes") or {}
    raw = attrs.get("path") if isinstance(attrs, dict) else None
    if isinstance(raw, str) and raw:
        parts = [p for p in raw.split("/") if p]
        return "/".join(parts[:3])
    return str(data.get("type") or "unknown")


def _serialize_layout_payload(
    layout_nodes: list[LayoutNodeInput],
    layout_edges: list[LayoutEdgeInput],
    meta: dict[str, dict[str, object]],
    *,
    total_nodes: int,
    truncated_nodes: bool = False,
    truncated_edges: bool = False,
) -> dict[str, object]:
    result = layout_graph(layout_nodes, layout_edges)
    nodes_out: list[dict[str, object]] = []
    for pn in result.nodes:
        m = meta[pn.kg_id]
        node: dict[str, object] = {
            "id": pn.render_id, "kg_id": pn.kg_id, "x": pn.x, "y": pn.y, "z": pn.z,
            "label": m["label"], "name": m["name"], "size": pn.size,
            "color": m["color"], "in_calls": 0,
        }
        if m.get("file_path") is not None:
            node["file_path"] = m["file_path"]
        nodes_out.append(node)
    payload: dict[str, object] = {
        "nodes": nodes_out,
        "edges": [{"source": e.source, "target": e.target, "type": e.type} for e in result.edges],
        "total_nodes": total_nodes, "linked_projects": [],
        "truncated_nodes": truncated_nodes, "truncated_edges": truncated_edges,
    }
    if len(json_bytes(payload)) > _MAX_BYTES:
        raise PayloadTooLarge("serialized layout payload exceeds 4 MiB")
    return payload


def build_layout_payload(
    adapter: StorageAdapter,
    *,
    max_nodes: int = _MAX_NODES,
    max_edges: int = _MAX_EDGES,
) -> dict[str, object]:
    return _build_layout_payload_impl(adapter, max_nodes=max_nodes, max_edges=max_edges)


def build_snapshot_payload(adapter: StorageAdapter) -> dict[str, object]:
    return build_layout_payload(adapter, max_nodes=_MAX_NODES, max_edges=_MAX_EDGES)


def _build_layout_payload_impl(
    adapter: StorageAdapter,
    *,
    max_nodes: int = _MAX_NODES,
    max_edges: int = _MAX_EDGES,
) -> dict[str, object]:
    if max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")
    if max_edges < 0:
        raise ValueError("max_edges must be >= 0")

    total_nodes = adapter.conn.execute(
        "SELECT COUNT(*) AS c FROM nodes WHERE status='active'"
    ).fetchone()["c"]

    n_rows = adapter.conn.execute(
        "SELECT id, data FROM nodes WHERE status='active' ORDER BY id LIMIT ?",
        (max_nodes + 1,),
    ).fetchall()
    truncated_nodes = len(n_rows) > max_nodes
    n_rows = n_rows[:max_nodes]
    retained_ids = {r["id"] for r in n_rows}

    # Candidate edges among ALL active nodes, pre node-truncation, so edge
    # truncation reflects the true edge count before dangling-edge dropping.
    e_rows = adapter.conn.execute(
        "SELECT e.id, e.source, e.target, e.semantic_type FROM edges e "
        "JOIN nodes sn ON sn.id = e.source AND sn.status='active' "
        "JOIN nodes tn ON tn.id = e.target AND tn.status='active' "
        "WHERE e.status='active' ORDER BY e.id LIMIT ?",
        (max_edges + 1,),
    ).fetchall()
    truncated_edges = len(e_rows) > max_edges
    e_rows = e_rows[:max_edges]

    # Degree from retained active edges: both endpoints within the retained set.
    deg: dict[str, int] = {}
    if retained_ids:
        ids = list(retained_ids)
        placeholders = ",".join("?" * len(ids))
        for column in ("source", "target"):
            rows = adapter.conn.execute(
                f"SELECT e.{column} AS endpoint, COUNT(*) AS c FROM edges e "
                f"WHERE e.status='active' AND e.source IN ({placeholders}) "
                f"AND e.target IN ({placeholders}) GROUP BY e.{column}",
                [*ids, *ids],
            ).fetchall()
            for row in rows:
                deg[row["endpoint"]] = deg.get(row["endpoint"], 0) + row["c"]

    meta: dict[str, dict] = {}
    layout_nodes: list[LayoutNodeInput] = []
    for r in n_rows:
        kg_id = r["id"]
        try:
            data = json.loads(r["data"])
        except (ValueError, TypeError):
            data = {}
        node_type = str(data.get("type") or "unknown")
        name = str(data.get("name") or kg_id)
        attrs = data.get("attributes")
        raw_path = attrs.get("path") if isinstance(attrs, dict) else None
        file_path = raw_path if isinstance(raw_path, str) else None
        meta[kg_id] = {
            "label": node_type,
            "name": name,
            "color": _COLOR_BY_TYPE.get(node_type, _DEFAULT_COLOR),
            "file_path": file_path,
        }
        layout_nodes.append(
            LayoutNodeInput(
                kg_id, _cluster_key(data), name, node_type, deg.get(kg_id, 0)
            )
        )

    layout_edges = [
        LayoutEdgeInput(r["source"], r["target"], r["semantic_type"])
        for r in e_rows
    ]
    return _serialize_layout_payload(
        layout_nodes, layout_edges, meta,
        total_nodes=total_nodes,
        truncated_nodes=truncated_nodes,
        truncated_edges=truncated_edges,
    )


def build_project_payload(adapter: StorageAdapter, project_name: str) -> dict[str, object]:
    root = Path(adapter.db_path).expanduser().resolve().parent
    if root.name == ".kg":
        root = root.parent
    return {
        "name": project_name,
        "root_path": str(root),
        "indexed_at": str(adapter.generation()),
    }


def build_schema_payload(adapter: StorageAdapter) -> dict[str, object]:
    node_labels = [
        {"label": r["label"], "count": r["count"]}
        for r in adapter.conn.execute(
            "SELECT type AS label, COUNT(*) AS count FROM nodes "
            "WHERE status='active' GROUP BY type ORDER BY label"
        ).fetchall()
    ]
    edge_types = [
        {"type": r["type"], "count": r["count"]}
        for r in adapter.conn.execute(
            "SELECT semantic_type AS type, COUNT(*) AS count FROM edges "
            "WHERE status='active' GROUP BY semantic_type ORDER BY type"
        ).fetchall()
    ]
    total_nodes = adapter.conn.execute(
        "SELECT COUNT(*) AS c FROM nodes WHERE status='active'"
    ).fetchone()["c"]
    total_edges = adapter.conn.execute(
        "SELECT COUNT(*) AS c FROM edges WHERE status='active'"
    ).fetchone()["c"]
    return {
        "node_labels": node_labels,
        "edge_types": edge_types,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
    }
