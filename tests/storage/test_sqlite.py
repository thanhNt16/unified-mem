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
