from kg.community import louvain
from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter


def _adapter(tmp_path):
    return SQLiteAdapter(tmp_path / "kg.db")


def test_louvain_deterministic_two_cliques(tmp_path):
    adapter = _adapter(tmp_path)
    ids = [f"u:person:{i}" for i in range(6)]
    adapter.upsert_nodes([Node(id=node_id, type="person", name=node_id) for node_id in ids])
    adapter.upsert_edges([
        *[Edge(id=f"{a}|knows|{b}", semantic_type="knows")
          for a, b in [(ids[0], ids[1]), (ids[1], ids[2]), (ids[0], ids[2])]],
        *[Edge(id=f"{a}|knows|{b}", semantic_type="knows")
          for a, b in [(ids[3], ids[4]), (ids[4], ids[5]), (ids[3], ids[5])]],
    ])
    first = louvain(adapter)
    assert first == louvain(adapter)
    assert first[ids[0]] == first[ids[1]] == first[ids[2]]
    assert first[ids[3]] == first[ids[4]] == first[ids[5]]
    assert first[ids[0]] != first[ids[3]]


def test_louvain_node_cap_skips(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.upsert_nodes([
        Node(id=f"u:person:{i}", type="person", name=str(i))
        for i in range(5001)
    ])
    assert set(louvain(adapter).values()) == {-1}
