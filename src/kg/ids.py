# src/kg/ids.py
from __future__ import annotations
from kg.chunking import slugify


def node_id(user_id: str, type_: str, name: str) -> str:
    return f"{user_id}:{type_}:{slugify(name)}"


def edge_id(source_id: str, semantic_type: str, target_id: str) -> str:
    return f"{source_id}|{semantic_type}|{target_id}"
