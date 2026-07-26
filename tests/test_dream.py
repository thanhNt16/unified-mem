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


# --- Task 2: EXPIRING / ORPHAN / CONTRADICT ---------------------------------

def test_expiring_listed(tmp_path):
    """Active node with valid_until in the past surface as EXPIRING."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:preference:old", type="preference", name="likes-x",
                         valid_until="2020-01-01T00:00:00Z")])
    cands = dream_candidates(a, since=None)
    exp = [c for c in cands if c.reason == "expiring"]
    assert len(exp) == 1
    assert exp[0].node_ids == ["u:preference:old"]
    assert "2020-01-01T00:00:00Z" in exp[0].detail


def test_expiring_skips_future_valid_until(tmp_path):
    """valid_until in the future is NOT expiring."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:preference:future", type="preference", name="likes-y",
                         valid_until="9999-01-01T00:00:00Z")])
    cands = dream_candidates(a, since=None)
    assert not [c for c in cands if c.reason == "expiring"]


def test_expiring_skips_tombstoned(tmp_path):
    """Tombstoned nodes are excluded from every worklist sweep."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:preference:dead", type="preference", name="likes-z",
                         valid_until="2020-01-01T00:00:00Z")])
    a.delete("u:preference:dead")
    cands = dream_candidates(a, since=None)
    assert not [c for c in cands if c.reason == "expiring"]


def test_orphan_listed_when_no_sources(tmp_path):
    """Active node with empty sources is an ORPHAN candidate."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:fact:f1", type="fact", name="X is Y", sources=[])])
    cands = dream_candidates(a, since=None)
    orphans = [c for c in cands if c.reason == "orphan"]
    assert len(orphans) == 1
    assert orphans[0].node_ids == ["u:fact:f1"]
    assert orphans[0].detail == "orphan"


def test_orphan_not_flagged_when_sources_present(tmp_path):
    """NARROW ORPHAN rule: nonempty sources ⇒ NOT flagged."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:fact:f2", type="fact", name="X is Z",
                         sources=[{"doc": "raw/notes.md"}])])
    cands = dream_candidates(a, since=None)
    assert not [c for c in cands if c.reason == "orphan"]


def test_contradict_pair_same_name_diff_summary(tmp_path):
    """Two active facts sharing name with differing summary ⇒ CONTRADICT."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:fact:a", type="fact", name="sky color", summary="blue"),
        Node(id="u:fact:b", type="fact", name="sky color", summary="green"),
    ])
    cands = dream_candidates(a, since=None)
    contra = [c for c in cands if c.reason == "contradict"]
    assert len(contra) == 1
    assert set(contra[0].node_ids) == {"u:fact:a", "u:fact:b"}


def test_contradict_skips_identical_summary(tmp_path):
    """Same name AND same summary ⇒ no contradiction."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:fact:a", type="fact", name="sky color", summary="blue"),
        Node(id="u:fact:b", type="fact", name="sky color", summary="blue"),
    ])
    cands = dream_candidates(a, since=None)
    assert not [c for c in cands if c.reason == "contradict"]


def test_contradict_skips_different_type(tmp_path):
    """Only facts of the same type contribute to CONTRADICT sweep."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:fact:a", type="fact", name="sky color", summary="blue"),
        Node(id="u:preference:b", type="preference", name="sky color",
             summary="green"),
    ])
    cands = dream_candidates(a, since=None)
    assert not [c for c in cands if c.reason == "contradict"]


def test_contradict_no_duplicate_pairs_and_stable_order(tmp_path):
    """Pair appears at most once; ordering is deterministic (sorted by id)."""
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:fact:c", type="fact", name="temp", summary="hot"),
        Node(id="u:fact:a", type="fact", name="temp", summary="cold"),
        Node(id="u:fact:b", type="fact", name="temp", summary="warm"),
    ])
    cands = dream_candidates(a, since=None)
    contra = [c for c in cands if c.reason == "contradict"]
    # 3 choose 2 = 3 unique pairs; no dupes
    pair_sets = {frozenset(c.node_ids) for c in contra}
    assert len(pair_sets) == len(contra) == 3
    # Deterministic ordering: each pair sorted, and list sorted by pair tuple
    for c in contra:
        assert c.node_ids == sorted(c.node_ids)
    assert [tuple(c.node_ids) for c in contra] == sorted(
        tuple(c.node_ids) for c in contra
    )
