import json
from kg.ontology import (
    ALLOWED_NODE_TYPES, ALLOWED_SEMANTIC_EDGE_TYPES,
    STRUCTURAL_EDGE_TYPES,
    build_ontology_schema, write_ontology,
)


def test_pole_types_present():
    for t in ("person", "organization", "location", "event", "object",
              "preference", "fact", "document", "chunk", "conversation", "session"):
        assert t in ALLOWED_NODE_TYPES


def test_semantic_edges_present():
    for e in ("employed_by", "member_of", "knows", "located_at", "alias_of",
              "uses", "owns", "related_to"):
        assert e in ALLOWED_SEMANTIC_EDGE_TYPES


def test_structural_edges_present():
    for e in ("part_of", "next", "mentions", "same_as", "superseded_by"):
        assert e in STRUCTURAL_EDGE_TYPES


def test_semantic_and_structural_are_disjoint():
    assert ALLOWED_SEMANTIC_EDGE_TYPES.isdisjoint(STRUCTURAL_EDGE_TYPES)


def test_write_ontology_creates_valid_json(tmp_path):
    out = tmp_path / "ontology.json"
    write_ontology(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ontology_version"] == 1
    assert data["node_types"]
    assert data["edge_types"]["semantic"]
    assert data["edge_types"]["structural"]


def test_build_schema_includes_node_model():
    schema = build_ontology_schema()
    assert "Node" in schema["$defs"]
    assert "Edge" in schema["$defs"]
