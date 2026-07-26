from __future__ import annotations

import tiktoken

from kg.ontology import Node
from kg.pack import pack
from kg.storage.base import Subgraph

_ENC = tiktoken.get_encoding("cl100k_base")


def test_pack_returns_markdown_under_budget():
    nodes = [
        Node(id=f"u:person:{i}", type="person", name=f"Person {i}",
             summary="x" * 20, created_at="2026-07-26T00:00:00Z")
        for i in range(5)
    ]
    sg = Subgraph(nodes=nodes)
    seeds = {n.id: 0.9 for n in nodes}
    md = pack(sg, seeds, rrf_scores=seeds, budget_tokens=4000)
    assert isinstance(md, str)
    assert "Person 0" in md


def test_pack_trims_to_budget():
    big = [
        Node(id=f"u:person:{i}", type="person", name=f"P{i}",
             summary="word " * 400, created_at="2026-07-26T00:00:00Z")
        for i in range(20)
    ]
    sg = Subgraph(nodes=big)
    seeds = {n.id: 0.5 for n in big}
    md = pack(sg, seeds, rrf_scores=seeds, budget_tokens=500)
    assert len(md) < sum(len(n.summary or "") for n in big)


def test_pack_token_budget_respected():
    big = [
        Node(id=f"u:person:{i}", type="person", name=f"P{i}",
             summary="word " * 200, created_at="2026-07-26T00:00:00Z")
        for i in range(10)
    ]
    sg = Subgraph(nodes=big)
    seeds = {n.id: 0.5 for n in big}
    md = pack(sg, seeds, rrf_scores=seeds, budget_tokens=400)
    assert len(_ENC.encode(md)) <= 400


def test_pack_ranking_prefers_recent_over_older_equal_rrf():
    """Same RRF and same degree (0): more recent node ranks first."""
    older = Node(id="u:person:old", type="person", name="Old",
                 summary="s", created_at="2015-01-01T00:00:00Z")
    newer = Node(id="u:person:new", type="person", name="New",
                 summary="s", created_at="2026-07-26T00:00:00Z")
    sg = Subgraph(nodes=[older, newer])
    rrf_scores = {"u:person:old": 0.5, "u:person:new": 0.5}
    md = pack(sg, seeds={}, rrf_scores=rrf_scores, budget_tokens=4000)
    assert md.index("New") < md.index("Old")


def test_pack_ranking_prefers_higher_degree_over_equal_rrf_and_recency():
    """Same RRF and recency (no timestamps): higher-degree node ranks first."""
    from kg.ontology import Edge

    hub = Node(id="u:person:hub", type="person", name="Hub", summary="s")
    leaf = Node(id="u:person:leaf", type="person", name="Leaf", summary="s")
    other = Node(id="u:person:other", type="person", name="Other", summary="s")
    edges = [
        Edge(id="u:person:hub|related_to|u:person:leaf", semantic_type="related_to"),
        Edge(id="u:person:hub|related_to|u:person:other", semantic_type="related_to"),
    ]
    sg = Subgraph(nodes=[leaf, hub], edges=edges)
    rrf_scores = {"u:person:hub": 0.5, "u:person:leaf": 0.5}
    md = pack(sg, seeds={}, rrf_scores=rrf_scores, budget_tokens=4000)
    assert md.index("Hub") < md.index("Leaf")


def test_pack_dedupes_nodes():
    node = Node(id="u:person:dup", type="person", name="Dup", summary="s")
    sg = Subgraph(nodes=[node, node])
    md = pack(sg, seeds={}, rrf_scores={"u:person:dup": 0.5}, budget_tokens=4000)
    assert md.count("## Dup") == 1
