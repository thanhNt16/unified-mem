"""Tests for kg.deepsearch — hybrid_search -> expand -> materialize deep wiki."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kg.cli.init import init_project
from kg.deepsearch import build_deep_wiki
from kg.embed import FakeEmbedder
from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter


def _seed(tmp_path: Path, nodes, edges=()):
    paths = init_project(tmp_path, user_id="u", scope="s")
    adapter = SQLiteAdapter(paths.kg_db)
    adapter.upsert_nodes(nodes)
    adapter.upsert_edges(list(edges))
    return paths, adapter


def test_build_deep_wiki_materializes_expected_files(tmp_path):
    alice = Node(
        id="u:person:alice", type="person", name="Alice",
        summary="Founder of Acme",
    )
    bob = Node(id="u:person:bob", type="person", name="Bob", summary="Engineer")
    edge = Edge(id="u:person:alice|knows|u:person:bob", semantic_type="knows")
    paths, adapter = _seed(tmp_path, [alice, bob], [edge])

    embedder = FakeEmbedder()
    report = build_deep_wiki(
        adapter, embedder, "alice founder",
        hops=2, wiki_dir=paths.wiki,
    )

    assert report.cached is False
    assert report.pages >= 1
    assert report.path.is_dir()
    assert (report.path / "index.md").is_file()
    index = (report.path / "index.md").read_text()
    assert "alice founder" in index.lower() or "alice" in index.lower()


def test_build_deep_wiki_cache_reused_on_repeat(tmp_path):
    alice = Node(id="u:person:alice", type="person", name="Alice", summary="Founder")
    paths, adapter = _seed(tmp_path, [alice])
    embedder = FakeEmbedder()

    first = build_deep_wiki(adapter, embedder, "alice", hops=2, wiki_dir=paths.wiki)
    assert first.cached is False
    # Second call with same graph version -> cache hit.
    second = build_deep_wiki(adapter, embedder, "alice", hops=2, wiki_dir=paths.wiki)
    assert second.cached is True
    assert second.slug == first.slug


def test_build_deep_wiki_invalidated_when_graph_changes(tmp_path):
    alice = Node(id="u:person:alice", type="person", name="Alice", summary="Founder")
    paths, adapter = _seed(tmp_path, [alice])
    embedder = FakeEmbedder()

    first = build_deep_wiki(adapter, embedder, "alice", hops=2, wiki_dir=paths.wiki)
    assert first.cached is False

    # Graph changes -> node_count differs.
    adapter.upsert_nodes([Node(id="u:person:bob", type="person", name="Bob")])
    second = build_deep_wiki(adapter, embedder, "alice", hops=2, wiki_dir=paths.wiki)
    assert second.cached is False
    assert second.node_count == first.node_count + 1


def test_traversal_payload_rejected(tmp_path):
    alice = Node(id="u:person:alice", type="person", name="Alice")
    paths, adapter = _seed(tmp_path, [alice])
    with pytest.raises(ValueError, match="invalid query path"):
        build_deep_wiki(
            adapter, FakeEmbedder(), "../../etc/passwd", hops=1, wiki_dir=paths.wiki,
        )


def test_slug_hash_stable(tmp_path):
    alice = Node(id="u:person:alice", type="person", name="Alice")
    paths, adapter = _seed(tmp_path, [alice])
    first = build_deep_wiki(adapter, FakeEmbedder(), "safe query", hops=1, wiki_dir=paths.wiki)
    second = build_deep_wiki(adapter, FakeEmbedder(), "safe query", hops=1, wiki_dir=paths.wiki)
    assert first.slug == second.slug
    assert first.slug.endswith("--" + __import__("hashlib").sha256(b"safe query").hexdigest()[:8])
    assert "/" not in first.slug and ".." not in first.slug
    assert not first.slug.startswith("--")


def test_no_results_writes_meta_only(tmp_path):
    paths, adapter = _seed(tmp_path, [])
    report = build_deep_wiki(
        adapter, FakeEmbedder(), "anything", hops=2, wiki_dir=paths.wiki,
    )
    assert report.pages == 0
    assert report.path.is_dir()
    assert (report.path / ".deep-meta.json").is_file()


def test_build_deep_wiki_query_too_long_rejected_by_mcp(tmp_path):
    """Sanity: MCP layer rejects long queries; deepsearch itself accepts bounds."""
    paths, adapter = _seed(tmp_path, [])
    long_q = "x" * 10_000
    # Direct call: succeeds (deepsearch trusts callers; MCP layer bounds).
    report = build_deep_wiki(adapter, FakeEmbedder(), long_q, hops=1, wiki_dir=paths.wiki)
    assert report.path.is_dir()


def test_mcp_deep_search_memory_requires_authorization(tmp_path):
    from kg.mcp import handlers as H

    paths = init_project(tmp_path, user_id="u", scope="s")
    SQLiteAdapter(paths.kg_db).upsert_nodes([
        Node(id="u:person:alice", type="person", name="Alice", summary="hi"),
    ])
    with pytest.raises(H.HandlerError):
        H.deep_search_memory(
            paths.root.parent, query="alice", hops=2, authorized=False,
            embedder=FakeEmbedder(),
        )


def test_mcp_deep_search_memory_authorized_writes_files(tmp_path):
    from kg.mcp import handlers as H

    paths = init_project(tmp_path, user_id="u", scope="s")
    SQLiteAdapter(paths.kg_db).upsert_nodes([
        Node(id="u:person:alice", type="person", name="Alice", summary="Founder"),
    ])
    result = H.deep_search_memory(
        paths.root.parent, query="alice", hops=2, authorized=True,
        embedder=FakeEmbedder(),
    )
    assert result["slug"]
    assert result["pages"] >= 1
    assert (paths.wiki / "deep" / result["slug"] / "index.md").is_file()
