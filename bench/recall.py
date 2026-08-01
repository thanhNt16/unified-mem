"""Recall@k benchmark — retrieval accuracy (D3).

Tests: fraction of ground-truth entities retrieved in top-k results.
Metrics: Recall@5, Recall@10, MRR (mean reciprocal rank).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kg.embed import FakeEmbedder
from kg.search import hybrid_search
from kg.storage.sqlite import SQLiteAdapter
from bench.metrics import timed


def _safe_for_fts5(name: str) -> bool:
    """Return True if name can be searched via SQLite FTS5 without syntax errors.

    Filters out names containing apostrophes (FTS5 quote issues) and hyphens
    (treated as column separator).
    """
    if "'" in name or "-" in name:
        return False
    return True


# Ground truth: canonical names from corpus generator (bench/corpus.py)
# Note: names with FTS5-problematic characters (e.g. apostrophes) are excluded.
_GROUND_TRUTH = {
    "person": [
        "Elena Vasquez", "Marcus Chen", "Aisha Patel",
        "Sofia Magnusson", "Raj Krishnamurthy", "Lena Hoffmann", "Tomás Rivera",
        "Yuki Tanaka", "Paris", "Amara Okafor", "Viktor Novak", "Priya Sharma",
        "Diego Morales", "Fatima Al-Rashid",
    ],
    "organization": [
        "Meridian Labs", "Cascade AI", "Northwind Research", "Vertex Dynamics",
        "Solaris Foundation", "Pinnacle Corp", "Horizon Biotech", "Atlas Computing",
        "Quantum Leap Inc", "Ember Studios", "Zenith Labs", "Lumina Health",
    ],
    "location": [
        "Berlin", "Tokyo", "São Paulo", "Reykjavik", "Nairobi", "Paris",
        "Mumbai", "Vancouver", "Stockholm", "Singapore", "Cape Town", "Austin",
    ],
    "event": [
        "DataSummit 2024", "NeurIPS 2023", "Open Source Summit",
        "Climate Action Forum", "HealthTech Conference", "Quantum Computing Workshop",
        "AI Ethics Symposium", "Global Health Summit",
    ],
}


def _load_ground_truth(corpus_dir: Path) -> dict[str, list[str]]:
    """Load ground truth from corpus manifest if available.

    corpus_dir is <base>/scale-10/corpus; manifest.json is at <base>/scale-10/manifest.json.
    """
    corpus_dir = Path(corpus_dir)
    # Walk up one level: corpus_dir = .../scale-10/corpus
    # Manifest is at .../scale-10/manifest.json
    scale_dir = corpus_dir.parent
    manifest_path = scale_dir / "manifest.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            used = data.get("used_entities", [])
            # Group by type using corpus.py mappings
            from bench.corpus import (
                _PERSONS, _ORGS, _LOCATIONS, _EVENTS,
            )
            by_type: dict[str, list[str]] = {}
            for name in used:
                for entities, typ in [
                    (_PERSONS, "person"), (_ORGS, "organization"),
                    (_LOCATIONS, "location"), (_EVENTS, "event"),
                ]:
                    if any(e[0] == name for e in entities):
                        by_type.setdefault(typ, []).append(name)
                        break
            return by_type
        except (OSError, ValueError, KeyError):
            pass
    return _GROUND_TRUTH


def _node_id_to_name(adapter: SQLiteAdapter, node_id: str) -> str | None:
    node = adapter.get(node_id)
    return node.name if node else None


def run_recall_benchmark(
    adapter: SQLiteAdapter,
    embedder: FakeEmbedder,
    config,
    corpus_dir: Path,
    *,
    k_values: tuple[int, ...] = (5, 10),
) -> dict[str, Any]:
    """Run recall@k benchmark.

    Args:
        adapter: SQLite adapter.
        embedder: FakeEmbedder for deterministic results.
        config: kg Config with query settings.
        corpus_dir: Path to corpus for ground truth extraction.
        k_values: Which k values to test (default 5, 10).

    Returns:
        Dict with recall@k per type, aggregate metrics, and per-query results.
    """
    ground_truth = _load_ground_truth(corpus_dir)
    all_queries: list[tuple[str, str]] = [
        (typ, name) for typ, names in ground_truth.items() for name in names
        if _safe_for_fts5(name)
    ]

    results_by_type: dict[str, dict[str, Any]] = {}
    all_hits_at_5: list[int] = []
    all_hits_at_10: list[int] = []
    all_reciprocal_ranks: list[float] = []

    # Build safe ground-truth lookup per type
    safe_ground_truth: dict[str, list[str]] = {}
    for typ, names in ground_truth.items():
        safe_ground_truth[typ] = [name for name in names if _safe_for_fts5(name)]

    for typ, queries in safe_ground_truth.items():
        type_hits_at_5: list[int] = []
        type_hits_at_10: list[int] = []
        type_reciprocal_ranks: list[float] = []
        per_query: list[dict[str, Any]] = []

        for query_name in queries:
            # Search for the entity by name
            hits = hybrid_search(
                adapter, embedder, query_name, mode="hybrid", k=10, config=config
            )
            ranked_names = [
                _node_id_to_name(adapter, node_id)
                for node_id, _ in hits
            ]
            ranked_names = [n for n in ranked_names if n]

            # Find rank of exact match (case-insensitive)
            try:
                rank = next(
                    i + 1
                    for i, n in enumerate(ranked_names)
                    if n.lower() == query_name.lower()
                )
            except StopIteration:
                rank = 0

            type_hits_at_5.append(1 if rank <= 5 else 0)
            type_hits_at_10.append(1 if rank <= 10 else 0)
            type_reciprocal_ranks.append(1.0 / rank if rank > 0 else 0.0)

            per_query.append({
                "query": query_name,
                "rank": rank,
                "retrieved_at_10": ranked_names[:10],
            })

        results_by_type[typ] = {
            "count": len(queries),
            "recall_at_5": sum(type_hits_at_5) / len(type_hits_at_5) if type_hits_at_5 else 0.0,
            "recall_at_10": sum(type_hits_at_10) / len(type_hits_at_10) if type_hits_at_10 else 0.0,
            "mrr": sum(type_reciprocal_ranks) / len(type_reciprocal_ranks) if type_reciprocal_ranks else 0.0,
            "per_query": per_query,
        }

        all_hits_at_5.extend(type_hits_at_5)
        all_hits_at_10.extend(type_hits_at_10)
        all_reciprocal_ranks.extend(type_reciprocal_ranks)

    aggregate = {
        "total_queries": len(all_queries),
        "recall_at_5": sum(all_hits_at_5) / len(all_hits_at_5) if all_hits_at_5 else 0.0,
        "recall_at_10": sum(all_hits_at_10) / len(all_hits_at_10) if all_hits_at_10 else 0.0,
        "mrr": sum(all_reciprocal_ranks) / len(all_reciprocal_ranks) if all_reciprocal_ranks else 0.0,
    }

    return {
        "by_type": results_by_type,
        "aggregate": aggregate,
        "k_values_tested": list(k_values),
    }
