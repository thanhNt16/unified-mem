from kg.gate import Gate
from kg.storage.sqlite import SQLiteAdapter
from kg.resolve import Resolver
from kg.dedup import Deduper, DedupResult
from kg.embed import FakeEmbedder
from kg.config import Config
from kg.ontology import Node, Edge
from kg.ids import allocate_node_id, node_id, edge_id
import pytest


def _gate(tmp_path):
    cfg = Config.default()
    a = SQLiteAdapter(tmp_path / "kg.db")
    emb = FakeEmbedder()
    g = Gate(a, Resolver(a, emb, cfg.thresholds),
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


class _RecordingDeduper:
    def __init__(self, match_id=None, score=0.0):
        self.match_id = match_id
        self.score = score
        self.calls = []

    def dedup(self, node, embed_fields=None):
        self.calls.append(node)
        return DedupResult(self.match_id, self.score)


def test_same_name_distinct_contexts_both_persist(tmp_path):
    a, g, cfg = _gate(tmp_path)
    g.normalize(
        [{"type": "person", "name": "Paris", "summary": "capital of France"}],
        [], "raw/fr.md#chunk-0",
    )
    recorder = _RecordingDeduper(node_id("u", "person", "Paris"), 0.1)
    g.deduper = recorder

    rep = g.normalize(
        [{"type": "person", "name": "Paris", "summary": "city in Texas, USA"}],
        [], "raw/tx.md#chunk-0",
    )

    active = a.conn.execute(
        "SELECT data FROM nodes WHERE status='active'"
    ).fetchall()
    people = [Node.model_validate_json(r["data"]) for r in active]
    assert len(people) == 2
    assert {n.id for n in people} == {
        node_id("u", "person", "Paris"),
        f'{node_id("u", "person", "Paris")}-2',
    }
    assert rep.decisions[0].action == "NEW"
    assert len(recorder.calls) == 1


def test_same_name_same_context_merges(tmp_path):
    a, g, cfg = _gate(tmp_path)
    record = [{"type": "person", "name": "Paris", "summary": "capital of France"}]
    g.normalize(record, [], "raw/fr.md#chunk-0")

    rep = g.normalize(record, [], "raw/fr.md#chunk-0")

    assert a.count()["nodes"] == 1
    assert rep.decisions[0].action in {"RESOLVED", "MERGED"}


def test_resolver_does_not_short_circuit_dedup(tmp_path):
    a, g, cfg = _gate(tmp_path)
    g.normalize([{"type": "person", "name": "Paris", "summary": "France"}], [],
                "raw/fr.md#chunk-0")
    recorder = _RecordingDeduper(node_id("u", "person", "Paris"), 0.1)
    g.deduper = recorder

    g.normalize([{"type": "person", "name": "Paris", "summary": "Texas"}], [],
                "raw/tx.md#chunk-0")

    assert len(recorder.calls) == 1


def test_allocate_node_id_uses_first_free_suffix(tmp_path):
    a, g, cfg = _gate(tmp_path)
    base = node_id("u", "person", "Paris")
    a.upsert_nodes([
        Node(id=base, type="person", name="Paris"),
        Node(id=f"{base}-2", type="person", name="Paris"),
    ])
    assert allocate_node_id(a, "u", "person", "Paris") == f"{base}-3"


def test_merge_preserves_colliding_winner_edge_metadata(tmp_path):
    a, g, cfg = _gate(tmp_path)
    winner, loser, org = "u:person:a", "u:person:c", "u:organization:b"
    a.upsert_nodes([
        Node(id=winner, type="person", name="A"),
        Node(id=loser, type="person", name="C"),
        Node(id=org, type="organization", name="B"),
    ])
    a.upsert_edges([
        Edge(id=edge_id(winner, "employed_by", org), semantic_type="employed_by",
             sources=[{"doc": "w"}], summary="winner", confidence=0.8),
        Edge(id=edge_id(loser, "employed_by", org), semantic_type="employed_by",
             sources=[{"doc": "l"}], summary="x", confidence=0.7),
    ])

    g.merge(winner, loser)

    row = a.conn.execute(
        "SELECT data FROM edges WHERE id=?", (edge_id(winner, "employed_by", org),)
    ).fetchone()
    merged = Edge.model_validate_json(row["data"])
    assert merged.sources == [{"doc": "w"}, {"doc": "l"}]
    assert merged.summary == "winner"
    assert merged.confidence == 0.8


def test_normalize_rolls_back_all_writes_on_mid_batch_failure(tmp_path, monkeypatch):
    a, g, cfg = _gate(tmp_path)

    def fail(_):
        raise RuntimeError("edge failure")

    monkeypatch.setattr(a, "upsert_edges", fail)
    with pytest.raises(RuntimeError, match="edge failure"):
        g.normalize(
            [{"type": "person", "name": "Alice"},
             {"type": "organization", "name": "Acme"}],
            [{"source_name": "Alice", "semantic_type": "employed_by",
              "target_name": "Acme"}],
            "raw/x.md#chunk-0",
        )
    assert a.count() == {"nodes": 0, "edges": 0}


def test_merge_rolls_back_winner_when_tombstone_fails(tmp_path, monkeypatch):
    a, g, cfg = _gate(tmp_path)
    winner = Node(id="u:person:w", type="person", name="Winner", aliases=["old"])
    loser = Node(id="u:person:l", type="person", name="Loser", aliases=["new"])
    a.upsert_nodes([winner, loser])
    original = a.upsert_nodes
    calls = 0

    def fail_second(nodes):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("tombstone failure")
        return original(nodes)

    monkeypatch.setattr(a, "upsert_nodes", fail_second)
    with pytest.raises(RuntimeError, match="tombstone failure"):
        g.merge(winner.id, loser.id)
    assert a.get(winner.id).aliases == ["old"]
    assert a.get(loser.id).status == "active"


def test_fact_persists_embeds_dedups_and_skips_resolver(tmp_path, monkeypatch):
    a, g, cfg = _gate(tmp_path)
    calls = []
    original = g.resolver.resolve

    def record_resolve(name, type_):
        calls.append((name, type_))
        return original(name, type_)

    monkeypatch.setattr(g.resolver, "resolve", record_resolve)
    fact = {"subject": "AlphaFold 3", "predicate": "released_in", "object": "2024"}
    first = g.normalize([], [], "raw/a.md#chunk-0", facts=[fact])
    second = g.normalize([], [], "raw/a.md#chunk-0", facts=[fact])

    stored = [Node.model_validate_json(r["data"]) for r in a.conn.execute(
        "SELECT data FROM nodes WHERE status='active'"
    ).fetchall()]
    assert len(stored) == 1
    assert stored[0].type == "fact"
    assert stored[0].embedding is not None
    assert stored[0].attributes == fact
    assert calls == []
    assert first.decisions[0].action == "NEW"
    assert second.decisions[0].action in {"RESOLVED", "MERGED"}


def test_missing_edge_is_reported(tmp_path):
    a, g, cfg = _gate(tmp_path)
    rep = g.normalize(
        [{"type": "person", "name": "Alice"}],
        [{"source_name": "Alice", "semantic_type": "employed_by",
          "target_name": "Missing Org"}],
        "raw/x.md#chunk-0",
    )
    assert rep.dropped_edges == [{
        "source_name": "Alice",
        "target_name": "Missing Org",
        "semantic_type": "employed_by",
        "reason": "missing",
    }]


def test_source_without_chunk_token_defaults_chunk_zero(tmp_path):
    a, g, cfg = _gate(tmp_path)
    g.normalize([{"type": "person", "name": "Alice"}], [], "raw/x.md")
    assert a.get(node_id("u", "person", "Alice")).sources == [
        {"doc": "raw/x.md", "chunk": "0"}
    ]
