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


# --- Task 6: FTS5 + sqlite-vec search ---
def test_fts_search(tmp_path):
    a = _adapter(tmp_path)
    a.upsert_nodes([
        Node(id="u:person:d", type="person", name="Demis Hassabis", summary="DeepMind founder"),
        Node(id="u:person:o", type="person", name="Other", summary="unrelated"),
    ])
    hits = a.fts_search("DeepMind founder", k=5)
    ids = [h[0] for h in hits]
    assert "u:person:d" in ids
    assert ids[0] == "u:person:d"


def test_fts_search_type_filter(tmp_path):
    a = _adapter(tmp_path)
    a.upsert_nodes([
        Node(id="u:person:d", type="person", name="Demis", summary="x"),
        Node(id="u:object:d2", type="object", name="Demis-tool", summary="x"),
    ])
    hits = a.fts_search("Demis", k=5, type_filter="person")
    assert all(h[0].startswith("u:person:") for h in hits)


def test_vec_search(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    base = [1.0] * 384
    n1 = Node(id="u:person:a", type="person", name="A", embedding=base)
    n2 = Node(id="u:person:b", type="person", name="B",
              embedding=[0.5 if i == 0 else 1.0 for i in range(384)])
    a.upsert_nodes([n1, n2])
    hits = a.vec_search(base, k=2)
    assert hits[0][0] == "u:person:a"  # identical → highest cosine


def test_vec_search_type_filter_overfetches_wrong_type_hits(tmp_path):
    a = _adapter(tmp_path)
    query = [1.0] + [0.0] * 383
    wrong_types = [
        Node(
            id=f"u:organization:{i}",
            type="organization",
            name=f"Org {i}",
            embedding=query,
        )
        for i in range(11)
    ]
    person = Node(
        id="u:person:match",
        type="person",
        name="Match",
        embedding=[0.9] + [0.0] * 383,
    )
    a.upsert_nodes([*wrong_types, person])

    hits = a.vec_search(query, k=1, type_filter="person")

    assert hits == [("u:person:match", hits[0][1])]


# Note: existing-node re-embedding remains M1-simple; cache stored vectors when scale demands it.
# Note: Resolver.user_id is retained for its public interface, unused by naming-only resolution.
