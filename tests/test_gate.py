from kg.gate import Gate
from kg.storage.sqlite import SQLiteAdapter
from kg.resolve import Resolver
from kg.dedup import Deduper, DedupResult
from kg.embed import FakeEmbedder
from kg.config import Config
from kg.ontology import Node, Edge
from kg.ids import node_id, edge_id


def _gate(tmp_path):
    cfg = Config.default()
    a = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    g = Gate(a, Resolver(a, emb, cfg.thresholds, "u"),
             Deduper(a, emb, cfg.thresholds), emb, cfg, user_id="u")
    return a, g, cfg


def test_new_node_routed_as_new(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Demis Hassabis", "summary": "founder"}],
        [], source="raw/x.md#chunk-0",
    )
    assert rep.decisions[0].action == "NEW"
    assert a.count()["nodes"] == 1


def test_idempotent_rerun(tmp_path):
    a, g, cfg = _gate(tmp_path)
    nodes = [{"type": "person", "name": "Demis Hassabis", "summary": "founder"}]
    g.normalize(nodes, [], "raw/x.md#chunk-0")
    g.normalize(nodes, [], "raw/x.md#chunk-0")
    assert a.count()["nodes"] == 1


def test_rejects_unknown_type(tmp_path):
    import pytest
    a, g, cfg = _gate(tmp_path)
    with pytest.raises(ValueError):
        g.normalize([{"type": "alien", "name": "X"}], [], "raw/x.md#chunk-0")


def test_gray_zone_flagged_not_merged(tmp_path):
    a, g, cfg = _gate(tmp_path)
    a.upsert_nodes([Node(id="u:person:existing", type="person", name="Existing Name")])
    g.deduper = _ForcedFlagDeduper("u:person:existing", 0.90)
    rep = g.normalize([{"type": "person", "name": "Different Name", "summary": "x"}],
                      [], "raw/b.md#chunk-0")
    assert "MERGED" not in [d.action for d in rep.decisions]
    assert a.count()["nodes"] == 2


def test_edge_name_resolution(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Demis Hassabis"},
         {"type": "organization", "name": "DeepMind"}],
        [{"source_name": "Demis Hassabis", "semantic_type": "employed_by",
          "target_name": "DeepMind"}],
        "raw/x.md#chunk-0",
    )
    assert rep.edges_upserted == 1
    assert a.count()["edges"] == 1


def test_merge_union_and_tombstone(tmp_path):
    a, g, cfg = _gate(tmp_path)
    a.upsert_nodes([
        Node(id="u:person:w", type="person", name="Paris",
             aliases=["a"], sources=[{"doc": "x"}]),
        Node(id="u:person:l", type="person", name="Paree",
             aliases=["b"], sources=[{"doc": "y"}]),
    ])
    g.merge("u:person:w", "u:person:l")
    winner = a.get("u:person:w")
    loser = a.get("u:person:l")
    assert "b" in winner.aliases and "a" in winner.aliases
    assert {"doc": "x"} in winner.sources and {"doc": "y"} in winner.sources
    assert loser.status == "tombstoned"
    assert loser.merged_into == "u:person:w"


def test_merge_repoints_loser_edge(tmp_path):
    a, g, cfg = _gate(tmp_path)
    a.upsert_nodes([
        Node(id="u:person:w", type="person", name="Winner"),
        Node(id="u:person:l", type="person", name="Loser"),
        Node(id="u:organization:org", type="organization", name="Org"),
    ])
    old_id = "u:person:l|employed_by|u:organization:org"
    a.upsert_edges([Edge(
        id=old_id, semantic_type="employed_by", summary="works there",
        confidence=0.9, sources=[{"doc": "x"}],
    )])

    g.merge("u:person:w", "u:person:l")

    assert a.conn.execute("SELECT id FROM edges WHERE id=?", (old_id,)).fetchone() is None
    new_id = edge_id("u:person:w", "employed_by", "u:organization:org")
    row = a.conn.execute("SELECT data FROM edges WHERE id=?", (new_id,)).fetchone()
    assert row is not None
    new_edge = Edge.model_validate_json(row["data"])
    assert new_edge.summary == "works there"
    assert new_edge.confidence == 0.9
    assert new_edge.sources == [{"doc": "x"}]
    assert a.count()["edges"] == 1


class _ForcedMergeDeduper:
    def __init__(self, winner_id, score):
        self.winner_id, self.score = winner_id, score

    def dedup(self, node, embed_fields=None):
        return DedupResult(self.winner_id, self.score)


def test_auto_merge_persists_candidate_before_merge(tmp_path):
    a, g, cfg = _gate(tmp_path)
    a.upsert_nodes([Node(id="u:person:winner", type="person", name="Winner Name")])
    g.deduper = _ForcedMergeDeduper("u:person:winner", 0.99)

    rep = g.normalize(
        [{"type": "person", "name": "Totally Different Name", "summary": "x"}],
        [], "raw/x.md#chunk-0",
    )

    assert rep.decisions[0].action == "MERGED"
    assert rep.decisions[0].target_id == "u:person:winner"
    loser = a.get(node_id("u", "person", "Totally Different Name"))
    assert loser is not None
    assert loser.status == "tombstoned"
    assert loser.merged_into == "u:person:winner"
    assert a.get("u:person:winner").status == "active"


class _ForcedFlagDeduper:
    def __init__(self, match_id, score):
        self.match_id, self.score = match_id, score

    def dedup(self, node, embed_fields=None):
        return DedupResult(self.match_id, self.score)


def test_flagged_same_as_edge_is_pending(tmp_path):
    a, g, cfg = _gate(tmp_path)
    a.upsert_nodes([Node(id="u:person:existing", type="person", name="Existing Name")])
    g.deduper = _ForcedFlagDeduper("u:person:existing", 0.90)

    rep = g.normalize(
        [{"type": "person", "name": "Another Distinct Name", "summary": "y"}],
        [], "raw/b.md#chunk-0",
    )

    assert rep.decisions[0].action == "FLAGGED"
    cand = a.get(node_id("u", "person", "Another Distinct Name"))
    assert cand.status == "active"
    rows = a.conn.execute(
        "SELECT status, data FROM edges WHERE semantic_type='same_as'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert Edge.model_validate_json(rows[0]["data"]).status == "pending"


# --- Fix round 2: I1 extracted aliases preserved ---
def test_extracted_aliases_preserved_for_resolution(tmp_path):
    a, g, cfg = _gate(tmp_path)
    g.normalize(
        [{"type": "person", "name": "Paris", "aliases": ["City of Light"],
          "summary": "capital of France"}],
        [], "raw/x.md#chunk-0",
    )
    res = g.resolver.resolve("City of Light", "person")
    assert res.via == "exact"
    assert res.matched_id == node_id("u", "person", "Paris")


# --- Fix round 2: I2 duplicate names in one batch guarded, first-write-wins ---
def test_duplicate_name_in_batch_first_write_wins(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Dup", "summary": "first"},
         {"type": "person", "name": "Dup", "summary": "second"}],
        [], "raw/x.md#chunk-0",
    )
    assert a.count()["nodes"] == 1
    node = a.get(node_id("u", "person", "Dup"))
    assert node.summary == "first"
    assert len(rep.decisions) == 1


# --- Cross-type same-name collision (defect 2) ---
def test_cross_type_same_name_both_persist(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Paris", "summary": "a person"},
         {"type": "location", "name": "Paris", "summary": "a city"}],
        [], "raw/x.md#chunk-0",
    )
    assert a.count()["nodes"] == 2
    assert len(rep.decisions) == 2
    person = a.get(node_id("u", "person", "Paris"))
    location = a.get(node_id("u", "location", "Paris"))
    assert person is not None and location is not None
    assert person.id != location.id


def test_ambiguous_edge_endpoint_skipped(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Paris", "summary": "a person"},
         {"type": "location", "name": "Paris", "summary": "a city"},
         {"type": "organization", "name": "Acme"}],
        [{"source_name": "Paris", "semantic_type": "employed_by",
          "target_name": "Acme"}],
        "raw/x.md#chunk-0",
    )
    assert rep.edges_upserted == 0
    assert a.count()["edges"] == 0


def test_unambiguous_edge_endpoints_still_created(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Demis Hassabis"},
         {"type": "organization", "name": "DeepMind"}],
        [{"source_name": "Demis Hassabis", "semantic_type": "employed_by",
          "target_name": "DeepMind"}],
        "raw/x.md/chunk-0",
    )
    assert rep.edges_upserted == 1
    assert a.count()["edges"] == 1


def test_invalid_edge_type_prevents_any_node_write(tmp_path):
    import pytest
    a, g, cfg = _gate(tmp_path)
    with pytest.raises(ValueError, match="not_allowed"):
        g.normalize(
            [{"type": "person", "name": "Alice"}],
            [{"source_name": "Alice", "semantic_type": "not_allowed",
              "target_name": "Bob"}],
            "raw/x.md#chunk-0",
        )
    assert a.count()["nodes"] == 0


def test_extracted_edge_confidence_persists(tmp_path):
    a, g, cfg = _gate(tmp_path)
    g.normalize(
        [{"type": "person", "name": "Alice"},
         {"type": "organization", "name": "Acme"}],
        [{"source_name": "Alice", "semantic_type": "employed_by",
          "target_name": "Acme", "confidence": 0.9}],
        "raw/x.md#chunk-0",
    )
    edge = Edge.model_validate_json(
        a.conn.execute("SELECT data FROM edges").fetchone()["data"])
    assert edge.confidence == 0.9


def test_resolved_node_preserves_extracted_aliases(tmp_path):
    a, g, cfg = _gate(tmp_path)
    g.normalize([{"type": "location", "name": "Paris"}], [], "raw/x.md#chunk-0")
    g.normalize(
        [{"type": "location", "name": "Paris", "aliases": ["City of Light"]}],
        [], "raw/y.md#chunk-0",
    )
    resolved = g.resolver.resolve("City of Light", "location")
    assert resolved.matched_id == node_id("u", "location", "Paris")


def test_resolved_node_adds_source_once(tmp_path):
    a, g, cfg = _gate(tmp_path)
    g.normalize([{"type": "location", "name": "Paris"}], [], "raw/a.md#chunk-0")
    g.normalize([{"type": "location", "name": "Paris"}], [], "raw/b.md#chunk-1")
    g.normalize([{"type": "location", "name": "Paris"}], [], "raw/b.md#chunk-1")
    sources = a.get(node_id("u", "location", "Paris")).sources
    assert sources == [
        {"doc": "raw/a.md", "chunk": "0"},
        {"doc": "raw/b.md", "chunk": "1"},
]
