# src/kg/ids.py
from __future__ import annotations
from kg.chunking import slugify


def node_id(user_id: str, type_: str, name: str) -> str:
    return f"{user_id}:{type_}:{slugify(name)}"


def edge_id(source_id: str, semantic_type: str, target_id: str) -> str:
    return f"{source_id}|{semantic_type}|{target_id}"


def allocate_node_id(adapter, user_id: str, type_: str, name: str) -> str:
    """Return the base node_id if free in the adapter, else the first free
    `-N` suffix (starting at -2). Used when a content-derived id collides with
    a DISTINCT entity that dedup scored as different (spec §5.2).
    """
    base = node_id(user_id, type_, name)
    if adapter.get(base) is None:
        return base
    n = 2
    while adapter.get(f"{base}-{n}") is not None:
        n += 1
    return f"{base}-{n}"
