from __future__ import annotations
import json
from pathlib import Path
from pydantic import BaseModel, Field


ALLOWED_NODE_TYPES: set[str] = {
    "person", "organization", "location", "event", "object",
    "preference", "fact",
    "document", "chunk",
    "conversation", "session",
}

ALLOWED_SEMANTIC_EDGE_TYPES: set[str] = {
    "employed_by", "member_of", "knows", "located_at", "resides_at",
    "alias_of", "has_task", "uses", "owns", "related_to",
}

STRUCTURAL_EDGE_TYPES: set[str] = {
    "part_of", "next", "mentions", "same_as", "superseded_by",
}


class Node(BaseModel):
    id: str | None = None
    type: str
    subtype: str | None = None
    name: str
    canonical_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    summary: str | None = None
    attributes: dict = Field(default_factory=dict)
    valid_from: str | None = None
    valid_until: str | None = None
    sources: list[dict] = Field(default_factory=list)
    status: str = "active"


class Edge(BaseModel):
    id: str | None = None
    type: str = "related_to"
    semantic_type: str
    summary: str | None = None
    confidence: float = 0.0
    sources: list[dict] = Field(default_factory=list)
    valid_from: str | None = None
    valid_until: str | None = None


def build_ontology_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "node_types": sorted(ALLOWED_NODE_TYPES),
        "edge_types": {
            "semantic": sorted(ALLOWED_SEMANTIC_EDGE_TYPES),
            "structural": sorted(STRUCTURAL_EDGE_TYPES),
        },
        "$defs": {
            "Node": Node.model_json_schema(),
            "Edge": Edge.model_json_schema(),
        },
    }


def write_ontology(path: Path, ontology_version: int = 1) -> None:
    schema = build_ontology_schema()
    schema["ontology_version"] = ontology_version
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
