import json

import pytest

from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter
from kg.viz.api import (
    PayloadTooLarge,
    build_capabilities,
    build_layout_payload,
    build_project_payload,
    build_schema_payload,
    json_bytes,
)


def _seed(adapter: SQLiteAdapter) -> None:
    adapter.upsert_nodes([
        Node(id="doc:b", type="document", name="B", attributes={"path": "docs/b.md"}),
        Node(id="fact:a", type="fact", name="A"),
        Node(id="fact:c", type="fact", name="C"),
    ])
    adapter.upsert_edges([
        Edge(id="doc:b|mentions|fact:c", semantic_type="mentions"),
        Edge(id="fact:a|related_to|doc:b", semantic_type="related_to"),
    ])


def test_layout_payload_matches_pinned_cbm_shape(tmp_path) -> None:
    adapter = SQLiteAdapter(tmp_path / "graph.db")
    _seed(adapter)
    payload = build_layout_payload(adapter)

    assert set(payload) == {
        "nodes", "edges", "total_nodes", "linked_projects", "truncated_nodes", "truncated_edges"
    }
    assert payload["total_nodes"] == 3
    assert [n["kg_id"] for n in payload["nodes"]] == ["doc:b", "fact:a", "fact:c"]
    assert all(set(("id", "kg_id", "x", "y", "z", "label", "name", "size", "color", "in_calls")) <= set(n) for n in payload["nodes"])
    assert all("status" not in n and "qualified_name" not in n for n in payload["nodes"])
    assert payload["nodes"][0]["file_path"] == "docs/b.md"
    json.loads(json_bytes(payload))


def test_layout_payload_reports_limits(tmp_path) -> None:
    adapter = SQLiteAdapter(tmp_path / "graph.db")
    _seed(adapter)
    # One valid retained-node edge (doc:b - fact:a); a second valid edge
    # (doc:b - fact:c is dangling once fact:c truncates) does not count.
    adapter.upsert_edges([Edge(id="doc:b|related_to|fact:a", semantic_type="related_to")])
    payload = build_layout_payload(adapter, max_nodes=2, max_edges=1)
    assert len(payload["nodes"]) == 2
    assert len(payload["edges"]) == 1
    assert payload["truncated_nodes"] is True
    assert payload["truncated_edges"] is True


def test_truncated_nodes_do_not_consume_edge_budget(tmp_path) -> None:
    """Edges with a truncated endpoint must not consume the cap, and degree
    must derive from exactly the emitted edges."""
    adapter = SQLiteAdapter(tmp_path / "graph.db")
    # id order is "a", "b", "z"; the early edge below ties into truncated "z".
    adapter.upsert_nodes([
        Node(id="a", type="fact", name="A"),
        Node(id="b", type="fact", name="B"),
        Node(id="z", type="fact", name="Z"),
    ])
    adapter.upsert_edges([
        Edge(id="a|rel|z", source="a", target="z", semantic_type="related_to"),
        Edge(id="a|rel|b", source="a", target="b", semantic_type="related_to"),
    ])
    # max_nodes retains a,b; max_edges=1 fits the retained a-b edge exactly.
    payload = build_layout_payload(adapter, max_nodes=2, max_edges=1)
    edges = payload["edges"]
    assert len(edges) == 1
    assert {edges[0]["source"], edges[0]["target"]} == {0, 1}
    assert payload["truncated_nodes"] is True
    # The dangling a-z edge is dropped, not counted as truncation.
    assert payload["truncated_edges"] is False
    sizes = {n["kg_id"]: n["size"] for n in payload["nodes"]}
    assert sizes["a"] > 1
    assert sizes["b"] > 1


def test_capabilities_fail_closed_and_static_has_no_mutations() -> None:
    assert build_capabilities(static=True) == {
        "graph": True,
        "projects": False,
        "control": False,
        "index": False,
        "code_view": False,
        "adr": False,
        "dead_code": False,
        "missed_graph": False,
    }
    live = build_capabilities(static=False)
    assert live["control"] is False
    assert live["index"] is True
    assert live["projects"] is True
    assert live["graph"] is True
    assert live["adr"] is False


def test_validation_and_large_payload_errors(tmp_path) -> None:
    adapter = SQLiteAdapter(tmp_path / "graph.db")
    _seed(adapter)
    with pytest.raises(ValueError):
        build_layout_payload(adapter, max_nodes=0)
    with pytest.raises(ValueError):
        build_layout_payload(adapter, max_edges=-1)

    # Seed enough nodes that the serialized payload exceeds _MAX_BYTES.
    adapter.upsert_nodes([
        Node(id=f"big:{i}", type="document", name="x" * 256, attributes={"path": "src/a/b.py"})
        for i in range(18_000)
    ])
    with pytest.raises(PayloadTooLarge):
        build_layout_payload(adapter, max_nodes=20_000, max_edges=0)


def test_project_and_schema_payloads(tmp_path) -> None:
    adapter = SQLiteAdapter(tmp_path / "graph.db")
    _seed(adapter)
    project = build_project_payload(adapter, "demo")
    assert project["name"] == "demo"
    assert project["root_path"] == str(tmp_path.resolve())
    assert project["indexed_at"] == str(adapter.generation())

    schema = build_schema_payload(adapter)
    assert {n["label"] for n in schema["node_labels"]} == {"document", "fact"}
    assert {e["type"] for e in schema["edge_types"]} == {"mentions", "related_to"}
    assert schema["total_nodes"] == 3
    assert schema["total_edges"] == 2
