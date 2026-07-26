from __future__ import annotations

import json
from pathlib import Path

import pytest

from kg.cli.init import init_project
from kg.embed import FakeEmbedder
from kg.mcp.handlers import (
    ALL_TOOLS,
    HandlerError,
    dream_candidates_tool,
    expand_memory,
    merge_nodes,
    pack_context,
    read_ontology,
    read_wiki_index,
    review_confirm,
    review_reject,
    save_pole,
    search_memory,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    init_project(tmp_path, user_id="u", scope="s")
    return tmp_path


@pytest.fixture
def emb() -> FakeEmbedder:
    return FakeEmbedder()


def _save_persons(project: Path, emb: FakeEmbedder) -> dict:
    return save_pole(
        project,
        nodes=[
            {"type": "person", "name": "Demis Hassabis", "summary": "AI researcher"},
            {"type": "organization", "name": "DeepMind", "summary": "AI lab"},
        ],
        edges=[{
            "source_name": "Demis Hassabis", "target_name": "DeepMind",
            "semantic_type": "employed_by", "confidence": 1.0,
        }],
        source="raw/x.md#chunk-0",
        authorized=True,
        embedder=emb,
    )


def test_tool_schemas_closed_and_bounded():
    assert set(ALL_TOOLS) == {
        "search_memory", "expand_memory", "pack_context", "dream_candidates",
        "save_pole", "review_confirm", "review_reject", "merge_nodes",
        "deep_search_memory",
    }
    for spec in ALL_TOOLS.values():
        schema = spec["inputSchema"]
        assert schema["additionalProperties"] is False

    search = ALL_TOOLS["search_memory"]["inputSchema"]["properties"]
    assert search["k"]["maximum"] == 100
    assert search["query"]["maxLength"] > 0
    expand = ALL_TOOLS["expand_memory"]["inputSchema"]["properties"]
    assert expand["hops"]["maximum"] == 5
    assert expand["seed_ids"]["maxItems"] == 100
    deep = ALL_TOOLS["deep_search_memory"]["inputSchema"]["properties"]
    assert deep["hops"]["maximum"] == 5
    assert deep["query"]["maxLength"] > 0


def test_write_denied_by_default(project: Path, emb: FakeEmbedder):
    with pytest.raises(HandlerError) as exc:
        save_pole(
            project, nodes=[], edges=[], source="raw/x.md#chunk-0",
            embedder=emb,
        )
    assert exc.value.code == -32601
    assert "authorized" in exc.value.message

    for fn, kwargs in [
        (review_confirm, {"edge_id": "e", "winner_id": "w"}),
        (review_reject, {"edge_id": "e"}),
        (merge_nodes, {"winner_id": "w", "loser_id": "l"}),
    ]:
        with pytest.raises(HandlerError) as exc:
            fn(project, embedder=emb, **kwargs)
        assert exc.value.code == -32601


def test_save_search_expand_pack_and_dream(project: Path, emb: FakeEmbedder):
    saved = _save_persons(project, emb)
    assert saved["edges_upserted"] == 1
    assert len(saved["decisions"]) == 2

    found = search_memory(
        project, query="Demis", mode="keyword", k=10, embedder=emb,
    )
    assert found["results"]
    assert found["results"][0]["name"] == "Demis Hassabis"

    demis = next(d for d in saved["decisions"] if d["name"] == "Demis Hassabis")
    # IDs are intentionally omitted from save results; get via search.
    seed = found["results"][0]["node_id"]
    graph = expand_memory(project, seed_ids=[seed], hops=1)
    assert any(n["name"] == "Demis Hassabis" for n in graph["nodes"])
    assert all("embedding" not in n for n in graph["nodes"])

    packed = pack_context(project, seed_ids=[seed], hops=1, budget_tokens=512)
    assert "Demis Hassabis" in packed["markdown"]

    dreams = dream_candidates_tool(project)
    assert "candidates" in dreams


def test_invalid_inputs_rejected_before_gate(project: Path, emb: FakeEmbedder):
    with pytest.raises(HandlerError):
        search_memory(project, query="x", k=101, embedder=emb)
    with pytest.raises(HandlerError):
        expand_memory(project, seed_ids=["x"], hops=6)
    with pytest.raises(HandlerError):
        search_memory(project, query="x", mode="bogus", embedder=emb)
    with pytest.raises(HandlerError):
        save_pole(
            project,
            nodes=[{"type": "unknown", "name": "x"}],
            edges=[], source="raw/x.md#chunk-0",
            authorized=True, embedder=emb,
        )
    with pytest.raises(HandlerError):
        save_pole(
            project,
            nodes=[{"type": "person", "name": "x"}],
            edges=[{
                "source_name": "x", "target_name": "y",
                "semantic_type": "hacked",
            }],
            source="raw/x.md#chunk-0", authorized=True, embedder=emb,
        )


def test_project_root_required_and_arbitrary_paths_rejected(tmp_path: Path):
    with pytest.raises(HandlerError) as exc:
        search_memory(tmp_path, query="x", embedder=FakeEmbedder())
    assert ".kg" in exc.value.message

    not_dir = tmp_path / "file"
    not_dir.write_text("x")
    with pytest.raises(HandlerError):
        search_memory(not_dir, query="x", embedder=FakeEmbedder())


def test_symlinked_kg_rejected(tmp_path: Path):
    outside = tmp_path / "outside"
    init_project(outside, user_id="u", scope="s")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".kg").symlink_to(outside / ".kg", target_is_directory=True)

    with pytest.raises(HandlerError) as exc:
        search_memory(project, query="x", embedder=FakeEmbedder())
    assert "symlink" in exc.value.message


def test_resource_reads_bounded_and_path_contained(project: Path):
    ontology = json.loads(read_ontology(project))
    assert "node_types" in ontology
    assert "Memory Index" in read_wiki_index(project)

    # Callers cannot name a resource path; only the two fixed handlers exist.
    # Oversize file is capped instead of blowing a protocol frame.
    index = project / ".kg" / "wiki" / "index.md"
    index.write_text("x" * 300_000)
    assert len(read_wiki_index(project).encode()) <= 256_000


def test_review_confirm_preserves_pending_and_winner_semantics(project: Path, emb: FakeEmbedder):
    # Build a pending same_as directly to make the test deterministic across
    # embedder scoring variations; review semantics is what we care about here.
    from kg.ontology import Edge, Node
    from kg.ids import edge_id
    from kg.storage.sqlite import SQLiteAdapter

    ad = SQLiteAdapter(project / ".kg" / "kg.db")
    a = Node(id="person:alice-a", type="person", name="Alice A",
             embedding=emb.embed("Alice A"))
    b = Node(id="person:alice-b", type="person", name="Alice B",
             embedding=emb.embed("Alice B"))
    ad.upsert_nodes([a, b])
    eid = edge_id(a.id, "same_as", b.id)
    ad.upsert_edges([Edge(
        id=eid, semantic_type="same_as", status="pending", confidence=0.9,
    )])

    pending = dream_candidates_tool(project, kind="pending")["candidates"]
    assert pending, "directly-inserted pending edge must surface"
    edge = pending[0]
    winner = a.id

    with pytest.raises(HandlerError):
        review_confirm(
            project, edge_id=eid, winner_id="not-an-endpoint",
            authorized=True, embedder=emb,
        )

    result = review_confirm(
        project, edge_id=eid, winner_id=winner,
        authorized=True, embedder=emb, reason="agreed",
    )
    assert result["action"] == "review_confirm"
    assert result["winner_id"] == winner
    assert result["reason"] == "agreed"
    # Edge consumed by review.
    row = ad.conn.execute("SELECT id FROM edges WHERE id=?", (eid,)).fetchone()
    assert row is None


def test_review_reject_marks_pending_edge_rejected(project: Path, emb: FakeEmbedder):
    # Build a pending same_as directly through SQLite to isolate review semantics.
    from kg.ontology import Edge, Node
    from kg.ids import edge_id
    from kg.storage.sqlite import SQLiteAdapter

    ad = SQLiteAdapter(project / ".kg" / "kg.db")
    a = Node(id="person:a", type="person", name="A", embedding=emb.embed("A"))
    b = Node(id="person:b", type="person", name="B", embedding=emb.embed("B"))
    ad.upsert_nodes([a, b])
    eid = edge_id(a.id, "same_as", b.id)
    ad.upsert_edges([Edge(
        id=eid, semantic_type="same_as", status="pending", confidence=0.9,
    )])

    result = review_reject(
        project, edge_id=eid, authorized=True, embedder=emb,
    )
    assert result["action"] == "review_reject"
    row = ad.conn.execute("SELECT status FROM edges WHERE id=?", (eid,)).fetchone()
    assert row["status"] == "rejected"
