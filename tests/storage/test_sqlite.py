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


def test_neighbors_skips_pending_edges(tmp_path):
    """Pending edges must not be traversed by the recursive CTE."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="A"),
        Node(id="u:person:b", type="person", name="B"),
        Node(id="u:person:c", type="person", name="C"),
    ])
    a.upsert_edges([
        Edge(id="u:person:a|knows|u:person:b", semantic_type="knows", status="active"),
        Edge(id="u:person:b|knows|u:person:c", semantic_type="knows", status="pending"),
    ])
    sg = a.neighbors(["u:person:a"], depth=2)
    ids = {n.id for n in sg.nodes}
    assert "u:person:b" in ids
    assert "u:person:c" not in ids


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
    ids = [h[0] for h in hits]
    assert "u:person:d" in ids
    assert "u:object:d2" not in ids


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


def test_vec_upsert_reembed_updates_vector(tmp_path):
    """Upserting same active node with different embedding updates the vec row."""
    a = _adapter(tmp_path)
    v1 = [1.0] * 384
    v2 = [0.0] * 383 + [1.0]
    a.upsert_nodes([
        Node(id="u:person:x", type="person", name="X", embedding=v1),
        Node(id="u:person:y", type="person", name="Y", embedding=v1),
    ])
    a.upsert_nodes([Node(id="u:person:x", type="person", name="X", embedding=v2)])
    vec_count = a.conn.execute("SELECT COUNT(*) FROM nodes_vec WHERE node_id=?", ("u:person:x",)).fetchone()[0]
    assert vec_count == 1
    assert a.vec_search(v1, k=1, type_filter="person")[0][0] == "u:person:y"
    assert a.vec_search(v2, k=1, type_filter="person")[0][0] == "u:person:x"


def test_tombstone_removes_vec_row(tmp_path):
    """Tombstoning a formerly embedded node removes its vec row; vec_search excludes it."""
    a = _adapter(tmp_path)
    v = [1.0] + [0.0] * 383
    a.upsert_nodes([Node(id="u:person:x", type="person", name="X", embedding=v)])
    a.delete("u:person:x")  # tombstones
    vec_count = a.conn.execute("SELECT COUNT(*) FROM nodes_vec WHERE node_id=?", ("u:person:x",)).fetchone()[0]
    assert vec_count == 0
    hits = a.vec_search(v, k=5, type_filter="person")
    assert not any(h[0] == "u:person:x" for h in hits)


def test_hard_delete_removes_vec_and_edges(tmp_path):
    a = _adapter(tmp_path)
    v = [1.0] + [0.0] * 383
    a.upsert_nodes([Node(id="u:person:x", type="person", name="X", embedding=v)])
    a.upsert_edges([Edge(id="u:person:x|knows|u:person:y", semantic_type="knows")])
    assert a.count()["edges"] == 1
    a.delete("u:person:x", tombstone=False)
    vec_count = a.conn.execute("SELECT COUNT(*) FROM nodes_vec WHERE node_id=?", ("u:person:x",)).fetchone()[0]
    assert vec_count == 0
    assert a.count()["edges"] == 0


def test_transaction_rolls_back_multiple_upserts(tmp_path):
    a = _adapter(tmp_path)
    try:
        with a.transaction():
            a.upsert_nodes([
                Node(id="u:person:a", type="person", name="A"),
                Node(id="u:person:b", type="person", name="B"),
            ])
            raise RuntimeError("abort")
    except RuntimeError:
        pass
    assert a.count()["nodes"] == 0


# Note: existing-node re-embedding remains M1-simple; cache stored vectors when scale demands it.
