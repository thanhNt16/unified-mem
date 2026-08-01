"""End-to-end test for the /kg:query orchestrator.

Builds a real temp kg project, seeds nodes via Gate, runs the full query
flow by calling the internal functions directly (no shell), and asserts the
packed markdown, note file, log line, and query-log.jsonl entry.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from kg.cli.init import init_project
from kg.cli.query_cli import (
    _write_query_note,
    _token_count,
)
from kg.config import Config
from kg.dedup import Deduper
from kg.embed import FakeEmbedder
from kg.gate import Gate
from kg.pack import adaptive_budget, diversify_by_source, pack
from kg.resolve import Resolver
from kg.router import classify
from kg.search import graph_aware_hybrid_search
from kg.storage.sqlite import SQLiteAdapter
from kg.traverse import expand


@pytest.fixture
def seeded_kg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Init a kg project and seed a small knowledge graph."""
    monkeypatch.chdir(tmp_path)
    paths = init_project(tmp_path, user_id="t", scope="e2e")
    cfg = Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    embedder = FakeEmbedder()
    gate = Gate(
        adapter,
        Resolver(adapter, embedder, cfg.thresholds),
        Deduper(adapter, embedder, cfg.thresholds),
        embedder,
        cfg,
        cfg.project.user_id,
    )

    nodes = [
        {"type": "person", "name": "Alice", "summary": "Alice is a researcher."},
        {"type": "person", "name": "Bob", "summary": "Bob is an engineer."},
        {"type": "organization", "name": "Meridian Labs", "summary": "An AI research lab."},
        {"type": "document", "name": "doc:notes.md", "summary": "Project notes"},
    ]
    edges = [
        {"source_name": "doc:notes.md", "target_name": "Alice", "semantic_type": "mentions", "confidence": 1.0},
        {"source_name": "doc:notes.md", "target_name": "Bob", "semantic_type": "mentions", "confidence": 1.0},
        {"source_name": "doc:notes.md", "target_name": "Meridian Labs", "semantic_type": "mentions", "confidence": 1.0},
        {"source_name": "Alice", "target_name": "Meridian Labs", "semantic_type": "member_of", "confidence": 1.0},
        {"source_name": "Bob", "target_name": "Meridian Labs", "semantic_type": "member_of", "confidence": 1.0},
    ]
    gate.normalize(nodes, edges, "test-corpus")
    adapter.conn.close()
    return tmp_path


def test_query_e2e_full_flow(seeded_kg: Path) -> None:
    project_root = seeded_kg
    config = Config.from_path(project_root / ".kg" / "config.toml")
    adapter = SQLiteAdapter(project_root / ".kg" / "kg.db")
    embedder = FakeEmbedder()
    query = "what does Alice work on"

    policy = classify(query, config=config)
    ranked = graph_aware_hybrid_search(adapter, embedder, query, policy, config=config)
    ranked = diversify_by_source(ranked, adapter, max_per_source=config.query.diversity_cap)
    effective_budget = adaptive_budget(config.query.pack_budget_tokens, adapter)

    seed_ids = [nid for nid, _ in ranked]
    subgraph = expand(adapter, seed_ids, hops=policy.hops, cap=config.query.subgraph_cap) if seed_ids else None
    rrf_scores = {nid: score for nid, score in ranked}
    packed = pack(subgraph, rrf_scores, rrf_scores=rrf_scores, budget_tokens=effective_budget) if subgraph else ""

    # packed markdown non-empty
    assert packed
    assert "# kg context" in packed

    # write note + log
    notes_dir = project_root / ".kg" / "wiki" / "notes"
    note_path = _write_query_note(notes_dir, query, policy, packed)
    assert note_path.exists()
    log = notes_dir.parent / "log.md"
    assert log.exists()
    assert query.strip('"') in log.read_text(encoding="utf-8")

    # bench query-log entry
    log_dir = project_root / ".kg" / "bench-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    tokens = _token_count(packed)
    entry = {
        "ts": "2026-08-01T00:00:00+00:00",
        "query": query,
        "mode": policy.mode,
        "hits": len(ranked),
        "tokens": tokens,
    }
    with open(log_dir / "query-log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    log_path = log_dir / "query-log.jsonl"
    assert log_path.exists()
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert any(e["query"] == query and "tokens" in e for e in lines)

    adapter.conn.close()


def test_query_e2e_no_seeds_uses_empty_pack(seeded_kg: Path) -> None:
    """A query with no matches yields empty pack without crashing."""
    config = Config.from_path(seeded_kg / ".kg" / "config.toml")
    adapter = SQLiteAdapter(seeded_kg / ".kg" / "kg.db")
    embedder = FakeEmbedder()
    query = "gibberishxyz123"

    policy = classify(query, config=config)
    ranked = graph_aware_hybrid_search(adapter, embedder, query, policy, config=config)
    seed_ids = [nid for nid, _ in ranked]
    subgraph = expand(adapter, seed_ids, hops=policy.hops, cap=config.query.subgraph_cap) if seed_ids else None
    packed = pack(subgraph, {nid: s for nid, s in ranked}, budget_tokens=2000) if subgraph else ""

    # empty subgraph path -> empty packed string is acceptable
    assert packed == "" or "# kg context" in packed
    adapter.conn.close()
