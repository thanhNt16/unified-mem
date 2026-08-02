import pytest

from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter


@pytest.fixture
def seeded_adapter(tmp_path) -> SQLiteAdapter:
    adapter = SQLiteAdapter(tmp_path / "graph.db")
    adapter.upsert_nodes([
        Node(id="doc:b", type="document", name="B", attributes={"path": "docs/b.md"}),
        Node(id="fact:a", type="fact", name="A"),
        Node(id="fact:c", type="fact", name="C"),
    ])
    adapter.upsert_edges([
        Edge(id="doc:b|mentions|fact:c", semantic_type="mentions"),
        Edge(id="fact:a|related_to|doc:b", semantic_type="related_to"),
    ])
    return adapter
