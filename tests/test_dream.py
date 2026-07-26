from kg.dream import dream_candidates, Candidate
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node, Edge


def test_pending_same_as_listed(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:person:p1", type="person", name="Paris"),
                    Node(id="u:person:p2", type="person", name="Paree")])
    a.upsert_edges([Edge(id="u:person:p1|same_as|u:person:p2",
                         semantic_type="same_as", confidence=0.9, status="pending")])
    cands = dream_candidates(a, since=None)
    reasons = {c.reason for c in cands}
    assert "pending" in reasons
    p = [c for c in cands if c.reason == "pending"][0]
    assert set(p.node_ids) == {"u:person:p1", "u:person:p2"}
    assert p.score == 0.9


def test_recent_pair_pairs_same_type(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="Paris", canonical_name="Paris",
             created_at="2026-07-26T10:00:00Z"),
        Node(id="u:person:b", type="person", name="Paree", canonical_name="Paris",
             created_at="2026-07-26T10:00:00Z"),
        Node(id="u:object:c", type="object", name="Paris", canonical_name="Paris",
             created_at="2026-07-26T10:00:00Z"),
    ])
    cands = dream_candidates(a, since="2026-07-25")
    rp = [c for c in cands if c.reason == "recent-pair"]
    # same-type + near-canonical match → person pair flagged; object excluded by type
    assert any(set(c.node_ids) == {"u:person:a", "u:person:b"} for c in rp)
    assert all(c.node_type == "person" for c in rp)


def test_recent_pair_excludes_different_canonical(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="Alice",
             canonical_name="Alice", created_at="2026-07-26T10:00:00Z"),
        Node(id="u:person:b", type="person", name="Bob",
             canonical_name="Bob", created_at="2026-07-26T10:00:00Z"),
    ])
    cands = dream_candidates(a, since="2026-07-25")
    rp = [c for c in cands if c.reason == "recent-pair"]
    # Names clearly differ — no candidate.
    assert len(rp) == 0


def test_pending_excludes_non_pending_same_as(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:person:p1", type="person", name="Paris"),
                    Node(id="u:person:p2", type="person", name="Paree")])
    a.upsert_edges([Edge(id="u:person:p1|same_as|u:person:p2",
                         semantic_type="same_as", confidence=0.9, status="active")])
    cands = dream_candidates(a, since=None)
    assert not [c for c in cands if c.reason == "pending"]


def test_recent_pair_supports_duration_since(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="A", created_at="2020-01-01T00:00:00Z"),
        Node(id="u:person:b", type="person", name="A", created_at="2020-01-01T00:00:00Z"),
    ])
    assert not [c for c in dream_candidates(a, since="1d") if c.reason == "recent-pair"]


def test_recent_pair_respects_since_filter(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:old", type="person", name="Old", created_at="2024-01-01T00:00:00Z"),
        Node(id="u:person:new", type="person", name="New", created_at="2026-07-26T10:00:00Z"),
    ])
    cands = dream_candidates(a, since="2026-07-26")
    rp = [c for c in cands if c.reason == "recent-pair"]
    # Only one node passes since filter, so no pairs
    assert len(rp) == 0


def test_recent_pair_skips_tombstoned(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="A", created_at="2026-07-26T10:00:00Z"),
        Node(id="u:person:b", type="person", name="B", created_at="2026-07-26T10:00:00Z"),
    ])
    a.delete("u:person:b")  # tombstone
    cands = dream_candidates(a, since="2026-07-25")
    rp = [c for c in cands if c.reason == "recent-pair"]
    assert len(rp) == 0


def test_recent_pair_includes_recently_updated(tmp_path):
    """Controller spec: created_at OR updated_at in window qualifies node."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="Paris", canonical_name="Paris",
             created_at="2020-01-01T00:00:00Z",
             updated_at="2026-07-26T10:00:00Z"),
        Node(id="u:person:b", type="person", name="Paree", canonical_name="Paris",
             created_at="2026-07-26T10:00:00Z"),
    ])
    rp = [c for c in dream_candidates(a, since="2026-07-25")
          if c.reason == "recent-pair"]
    assert any(set(c.node_ids) == {"u:person:a", "u:person:b"} for c in rp)


def test_recent_pair_excludes_both_timestamps_old(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="Paris", canonical_name="Paris",
             created_at="2020-01-01T00:00:00Z",
             updated_at="2020-06-01T00:00:00Z"),
        Node(id="u:person:b", type="person", name="Paree", canonical_name="Paris",
             created_at="2020-01-01T00:00:00Z"),
    ])
    rp = [c for c in dream_candidates(a, since="2026-07-25")
          if c.reason == "recent-pair"]
    assert len(rp) == 0
