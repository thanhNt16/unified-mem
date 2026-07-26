from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node


def _adapter(tmp_path):
    return SQLiteAdapter(tmp_path / "kg.db")


def test_upsert_and_get(tmp_path):
    a = _adapter(tmp_path)
    n = Node(id="u:person:demis-hassabis", type="person", name="Demis Hassabis",
             summary="DeepMind founder")
    assert a.upsert_nodes([n]) == 1
    got = a.get("u:person:demis-hassabis")
    assert got is not None
    assert got.name == "Demis Hassabis"


def test_upsert_is_idempotent(tmp_path):
    a = _adapter(tmp_path)
    n = Node(id="u:person:x", type="person", name="X")
    a.upsert_nodes([n])
    a.upsert_nodes([n])  # same id -> upsert, not duplicate
    assert a.count()["nodes"] == 1


def test_delete_tombstones(tmp_path):
    a = _adapter(tmp_path)
    a.upsert_nodes([Node(id="u:person:x", type="person", name="X")])
    a.delete("u:person:x")
    got = a.get("u:person:x")
    assert got.status == "tombstoned"


def test_count_empty(tmp_path):
    a = _adapter(tmp_path)
    assert a.count() == {"nodes": 0, "edges": 0}


# --- Task 5: edges + neighbors ---
from kg.ontology import Edge


def _seed_graph(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="A"),
        Node(id="u:organization:b", type="organization", name="B"),
        Node(id="u:object:c", type="object", name="C"),
    ])
    a.upsert_edges([
        Edge(id="u:person:a|employed_by|u:organization:b", semantic_type="employed_by"),
        Edge(id="u:organization:b|owns|u:object:c", semantic_type="owns"),
    ])
    return a


def test_upsert_edges_and_count(tmp_path):
    a = _seed_graph(tmp_path)
    assert a.count()["edges"] == 2


def test_neighbors_one_hop(tmp_path):
    a = _seed_graph(tmp_path)
    sg = a.neighbors(["u:person:a"], depth=1)
    ids = {n.id for n in sg.nodes}
    assert "u:organization:b" in ids
    assert any(e.semantic_type == "employed_by" for e in sg.edges)


def test_neighbors_two_hop(tmp_path):
    a = _seed_graph(tmp_path)
    sg = a.neighbors(["u:person:a"], depth=2)
    ids = {n.id for n in sg.nodes}
    assert "u:object:c" in ids  # reached via b
