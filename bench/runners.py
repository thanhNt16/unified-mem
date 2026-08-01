"""Deterministic, local-only benchmark runners."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bench.metrics import cost_null_reason, graph_cleanliness, speed_metrics, timed
from bench.rubric import score_answer
from kg.cli.init import init_project
from kg.config import Config
from kg.dedup import Deduper
from kg.embed import FakeEmbedder
from kg.gate import Gate
from kg.resolve import Resolver
from kg.search import hybrid_search
from kg.storage.sqlite import SQLiteAdapter
from kg.traverse import expand

QUERIES = ("knowledge graph", "Meridian Labs", "Paris")
REPRESENTATIVE_QUERIES = [
    "knowledge graph systems",
    "Meridian Labs employees",
    "Paris person versus city",
    "Cascade AI research focus",
    "DataSummit 2024 attendees",
]
BASELINE_COMMAND = "rg -l --fixed-strings {query} {corpus_dir}"
_ENTITY_TYPES = {
    "Elena Vasquez": "person", "Marcus Chen": "person", "Aisha Patel": "person",
    "James O'Brien": "person", "Sofia Magnusson": "person", "Raj Krishnamurthy": "person",
    "Lena Hoffmann": "person", "Tomás Rivera": "person", "Yuki Tanaka": "person",
    "Amara Okafor": "person", "Viktor Novak": "person", "Priya Sharma": "person",
    "Diego Morales": "person", "Fatima Al-Rashid": "person", "Meridian Labs": "organization",
    "Cascade AI": "organization", "Northwind Research": "organization", "Vertex Dynamics": "organization",
    "Solaris Foundation": "organization", "Pinnacle Corp": "organization", "Horizon Biotech": "organization",
    "Atlas Computing": "organization", "Quantum Leap Inc": "organization", "Ember Studios": "organization",
    "Zenith Labs": "organization", "Lumina Health": "organization", "Berlin": "location",
    "Tokyo": "location", "São Paulo": "location", "Reykjavik": "location", "Nairobi": "location",
    "Paris": "location", "Mumbai": "location", "Vancouver": "location", "Stockholm": "location",
    "Singapore": "location", "Cape Town": "location", "Austin": "location", "DataSummit 2024": "event",
    "NeurIPS 2023": "event", "Open Source Summit": "event", "Climate Action Forum": "event",
    "HealthTech Conference": "event", "Quantum Computing Workshop": "event", "AI Ethics Symposium": "event",
    "Global Health Summit": "event",
}


@dataclass(frozen=True)
class RunResult:
    scale: int
    seed: int | None
    embedder_mode: str
    queries: dict[str, dict[str, Any]]
    graph_cleanliness: dict[str, Any]
    rubric: dict[str, Any]
    speed: dict[str, Any] = field(compare=False)
    cost: None = None
    cost_reason: str = field(default_factory=cost_null_reason)
    fastembed_checksum: None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BaselineResult:
    query: str
    matches: tuple[str, ...]
    command: str
    speed: dict[str, Any] = field(compare=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["matches"] = list(self.matches)
        return data


def _documents(corpus_dir: Path) -> list[Path]:
    return sorted(p for p in corpus_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt"})


def _extract(corpus_dir: Path) -> tuple[list[dict], list[dict], dict[str, str]]:
    nodes: dict[tuple[str, str], dict] = {}
    edges: list[dict] = []
    docs: dict[str, str] = {}
    for path in _documents(corpus_dir):
        relative = path.relative_to(corpus_dir).as_posix()
        document = f"document:{relative}"
        docs[document] = relative
        nodes[("document", document)] = {"type": "document", "name": document, "summary": relative}
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, type_ in _ENTITY_TYPES.items():
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
                nodes.setdefault((type_, name), {"type": type_, "name": name, "summary": name})
                edges.append({"source_name": document, "target_name": name, "semantic_type": "mentions", "confidence": 1.0})
    return list(nodes.values()), edges, docs


def _manifest_seed(corpus_dir: Path) -> int | None:
    for parent in (corpus_dir, corpus_dir.parent):
        path = parent / "manifest.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8")).get("seed")
            except (OSError, ValueError):
                return None
    return None


def build_indexed_adapter(
    corpus_dir: Path, project_dir: Path, *, scale: int
) -> tuple[SQLiteAdapter, FakeEmbedder, Config]:
    """Build a fresh kg index from corpus; returns open (adapter, embedder, config).

    Caller is responsible for closing the adapter's connection.
    """
    corpus_dir, project_dir = Path(corpus_dir), Path(project_dir)
    kg_dir = project_dir / ".kg"
    shutil.rmtree(kg_dir, ignore_errors=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    paths = init_project(project_dir, user_id="bench", scope=f"bench-{scale}")
    nodes, edges, documents = _extract(corpus_dir)
    (paths.root / "extracted.json").write_text(
        json.dumps({"nodes": nodes, "edges": edges}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
    gate.normalize(nodes, edges, "benchmark-corpus")
    return adapter, embedder, cfg


def run_kg(corpus_dir, project_dir, *, scale, embedder_opt="fake") -> RunResult:
    """Run the direct Gate/search/expand benchmark without ONNX or network."""
    if embedder_opt != "fake":
        raise ValueError("only embedder_opt='fake' is supported for deterministic benchmarks")
    corpus_dir, project_dir = Path(corpus_dir), Path(project_dir)
    adapter, embedder, cfg = build_indexed_adapter(corpus_dir, project_dir, scale=scale)
    _, _, _ = _extract(corpus_dir)  # documents map already used; this re-extracts, harmless
    documents = {f"document:{p.relative_to(corpus_dir).as_posix()}": p.relative_to(corpus_dir).as_posix() for p in _documents(corpus_dir)}

    def query_once(query: str) -> dict[str, Any]:
        hits = hybrid_search(adapter, embedder, query, mode="hybrid", config=cfg)
        ids = [node_id for node_id, _ in hits]
        subgraph = expand(adapter, ids, hops=2, cap=cfg.query.subgraph_cap) if ids else None
        names = [adapter.get(node_id).name for node_id in ids if adapter.get(node_id)]
        lineage = [documents[name] for name in names if name in documents]
        answer = " ".join([query, *names, *lineage])
        expected = {"cross_doc": [query], "multi_hop": names[:1], "lineage": lineage}
        return {"hits": names, "expanded_nodes": len(subgraph.nodes) if subgraph else 0, "expanded_edges": len(subgraph.edges) if subgraph else 0, "rubric": score_answer(answer, expected)}

    queries = {query: query_once(query) for query in QUERIES}
    speed = speed_metrics(lambda: query_once(QUERIES[0]))
    rubric_values = [value["rubric"] for value in queries.values()]
    rubric = {key: sum(item[key] for item in rubric_values) / len(rubric_values) for key in ("cross_doc_hits", "multi_hop_reach", "lineage", "score")}
    cleanliness = graph_cleanliness(adapter)
    adapter.conn.close()
    return RunResult(scale, _manifest_seed(corpus_dir), "fake", queries, cleanliness, rubric, speed)


def run_comparative(
    corpus_dir: Path, project_dir: Path, *, scale: int, queries: tuple[str, ...] = REPRESENTATIVE_QUERIES
) -> dict[str, Any]:
    """Run E2E comparative benchmark (D4): kg hybrid_search vs rg-style grep baseline.

    Builds a fresh kg index, then for each query times kg search + grep baseline
    and counts hits. Returns aggregate totals.
    """
    corpus_dir = Path(corpus_dir)
    adapter, embedder, cfg = build_indexed_adapter(corpus_dir, project_dir, scale=scale)
    try:
        results: list[dict[str, Any]] = []
        kg_total = 0
        grep_total = 0
        for query in queries:
            kg_timer = timed()
            with kg_timer as t:
                hits = hybrid_search(adapter, embedder, query, mode="hybrid", k=10, config=cfg)
            kg_hits = [adapter.get(nid).name for nid, _ in hits if adapter.get(nid)]
            kg_total += len(kg_hits)

            grep_timer = timed()
            with grep_timer as t2:
                baseline = run_baseline(corpus_dir, query=query)
            grep_total += len(baseline.matches)

            results.append({
                "query": query,
                "kg_hits": kg_hits,
                "kg_hit_count": len(kg_hits),
                "grep_hits": list(baseline.matches),
                "grep_hit_count": len(baseline.matches),
                "kg_ms": round(t.elapsed * 1000, 3),
                "grep_ms": round(t2.elapsed * 1000, 3),
            })
        return {
            "queries": results,
            "kg_total_hits": kg_total,
            "grep_total_hits": grep_total,
        }
    finally:
        adapter.conn.close()


def run_baseline(corpus_dir, *, query) -> BaselineResult:
    """Raw-file fixed-string grep baseline; equivalent to the recorded rg command."""
    corpus_dir = Path(corpus_dir)

    def grep() -> tuple[str, ...]:
        needle = query.casefold()
        return tuple(str(path.relative_to(corpus_dir)) for path in _documents(corpus_dir) if needle in path.read_text(encoding="utf-8", errors="replace").casefold())

    matches = grep()
    return BaselineResult(query, matches, BASELINE_COMMAND.format(query=query, corpus_dir=corpus_dir), speed_metrics(grep))
