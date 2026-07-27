"""Deterministic tests for bench.runners and bench.reporter (M6 T4).

No LLM, no network, no ONNX. Uses bench.corpus.make_corpus to seed a 10-doc
corpus, runs the kg pipeline via FakeEmbedder, and the grep baseline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# sqlite_vec is required transitively via kg.storage.sqlite; skip module if absent.
pytest.importorskip("sqlite_vec")

from bench.corpus import make_corpus  # noqa: E402
from bench.reporter import render_report  # noqa: E402
from bench.runners import (  # noqa: E402
    BASELINE_COMMAND,
    QUERIES,
    BaselineResult,
    RunResult,
    run_baseline,
    run_kg,
)


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    """Deterministic 10-doc corpus under tmp_path/corpus-root."""
    out = tmp_path / "corpus-root"
    make_corpus(10, out, seed=42)
    return out / "corpus"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


# --- run_kg ------------------------------------------------------------------

def test_run_kg_returns_run_result(corpus_dir: Path, project_dir: Path):
    result = run_kg(corpus_dir, project_dir, scale=10)
    assert isinstance(result, RunResult)
    assert result.scale == 10
    assert result.embedder_mode == "fake"


def test_run_kg_writes_kg_directory_and_db(corpus_dir: Path, project_dir: Path):
    run_kg(corpus_dir, project_dir, scale=10)
    assert (project_dir / ".kg").is_dir()
    assert (project_dir / ".kg" / "kg.db").is_file()


def test_run_kg_writes_extracted_json(corpus_dir: Path, project_dir: Path):
    run_kg(corpus_dir, project_dir, scale=10)
    extracted = project_dir / ".kg" / "extracted.json"
    assert extracted.is_file()
    payload = json.loads(extracted.read_text(encoding="utf-8"))
    assert "nodes" in payload and "edges" in payload
    assert isinstance(payload["nodes"], list) and len(payload["nodes"]) > 0


def test_run_kg_records_graph_cleanliness(corpus_dir: Path, project_dir: Path):
    result = run_kg(corpus_dir, project_dir, scale=10)
    gc = result.graph_cleanliness
    for key in ("total_nodes", "orphan_nodes", "score"):
        assert key in gc
    assert gc["total_nodes"] > 0
    assert 0.0 <= gc["score"] <= 1.0


def test_run_kg_records_speed_metrics(corpus_dir: Path, project_dir: Path):
    result = run_kg(corpus_dir, project_dir, scale=10)
    sp = result.speed
    for key in ("n", "min_seconds", "median_seconds", "p95_seconds", "max_seconds"):
        assert key in sp
    assert sp["n"] >= 1
    assert sp["min_seconds"] <= sp["median_seconds"] <= sp["max_seconds"]


def test_run_kg_runs_fixed_queries(corpus_dir: Path, project_dir: Path):
    result = run_kg(corpus_dir, project_dir, scale=10)
    for q in QUERIES:
        assert q in result.queries
        entry = result.queries[q]
        assert "hits" in entry and "expanded_nodes" in entry
        assert "expanded_edges" in entry and "rubric" in entry


def test_run_kg_records_rubric_score_in_unit_interval(corpus_dir: Path, project_dir: Path):
    result = run_kg(corpus_dir, project_dir, scale=10)
    rubric = result.rubric
    for key in ("cross_doc_hits", "multi_hop_reach", "lineage", "score"):
        assert key in rubric
        assert 0.0 <= rubric[key] <= 1.0


def test_run_kg_cost_null_with_documented_reason(corpus_dir: Path, project_dir: Path):
    result = run_kg(corpus_dir, project_dir, scale=10)
    assert result.cost is None
    assert "no usage tokens" in result.cost_reason.lower() or "claudedriver" in result.cost_reason.lower()


def test_run_kg_seed_propagated_from_manifest(corpus_dir: Path, project_dir: Path):
    result = run_kg(corpus_dir, project_dir, scale=10)
    # corpus fixture uses default seed=42 via make_corpus
    assert result.seed == 42


def test_run_kg_rejects_non_fake_embedder(corpus_dir: Path, project_dir: Path):
    with pytest.raises(ValueError):
        run_kg(corpus_dir, project_dir, scale=10, embedder_opt="onnx")


def test_run_kg_deterministic_on_rerun(corpus_dir: Path, project_dir: Path, tmp_path: Path):
    """Re-run on a fresh project dir must yield identical cleanliness + rubric."""
    first = run_kg(corpus_dir, project_dir, scale=10)
    second_dir = tmp_path / "project-2"
    second = run_kg(corpus_dir, second_dir, scale=10)
    assert first.graph_cleanliness == second.graph_cleanliness
    assert first.rubric == second.rubric
    assert first.queries.keys() == second.queries.keys()


def test_run_kg_fresh_kg_per_call_no_db_cross_contamination(
    corpus_dir: Path, project_dir: Path, tmp_path: Path
):
    first = run_kg(corpus_dir, project_dir, scale=10)
    second = run_kg(corpus_dir, tmp_path / "proj2", scale=10)
    assert first.graph_cleanliness["total_nodes"] == second.graph_cleanliness["total_nodes"]


# --- run_baseline -----------------------------------------------------------

def test_run_baseline_returns_baseline_result(corpus_dir: Path):
    result = run_baseline(corpus_dir, query="Paris")
    assert isinstance(result, BaselineResult)
    assert result.query == "Paris"
    assert isinstance(result.matches, tuple)
    assert all(isinstance(m, str) for m in result.matches)


def test_run_baseline_documents_exact_command(corpus_dir: Path):
    result = run_baseline(corpus_dir, query="Meridian Labs")
    # Command template references rg -l --fixed-strings, the query, and the corpus dir.
    assert "rg -l" in result.command
    assert "--fixed-strings" in result.command
    assert "Meridian Labs" in result.command
    assert str(corpus_dir) in result.command


def test_run_baseline_records_speed(corpus_dir: Path):
    result = run_baseline(corpus_dir, query="Tokyo")
    assert "n" in result.speed
    assert result.speed["n"] >= 1


def test_run_baseline_deterministic_matches(corpus_dir: Path):
    r1 = run_baseline(corpus_dir, query="Paris")
    r2 = run_baseline(corpus_dir, query="Paris")
    assert r1.matches == r2.matches


def test_run_baseline_case_insensitive(corpus_dir: Path):
    lower = run_baseline(corpus_dir, query="paris")
    upper = run_baseline(corpus_dir, query="PARIS")
    assert lower.matches == upper.matches


def test_run_baseline_no_match_yields_empty(corpus_dir: Path):
    result = run_baseline(corpus_dir, query="zzzNoSuchEntityzzz")
    assert result.matches == ()


# --- reporter ----------------------------------------------------------------

def test_render_report_writes_json_and_markdown(tmp_path: Path):
    payload = [
        {"scale": 10, "rubric": {"score": 0.5}, "graph_cleanliness": {"score": 0.9}},
    ]
    out = render_report(payload, [], out_path=tmp_path)
    assert out.name == "report.json"
    assert out.is_file()
    md = tmp_path / "report.md"
    assert md.is_file()


def test_render_report_json_contains_all_metric_keys(tmp_path: Path):
    kg = [{"scale": 10, "rubric": {"score": 0.42}, "graph_cleanliness": {"score": 0.9}}]
    baseline = [{"query": "Paris", "matches": ["a.md"], "command": "rg -l Paris"}]
    out = render_report(kg, baseline, out_path=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    for key in ("timestamp", "kg_results", "baseline_results", "tiers", "cost", "cost_reason"):
        assert key in data
    assert data["cost"] is None
    assert isinstance(data["cost_reason"], str) and data["cost_reason"]


def test_render_report_markdown_contains_tier_status(tmp_path: Path):
    kg = [{"scale": 10, "rubric": {"score": 0.5}, "graph_cleanliness": {"score": 0.9}}]
    out = render_report(kg, [], out_path=tmp_path)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    for scale in ("10", "50", "250"):
        assert scale in md


def test_render_report_marks_missing_tier_as_not_measured(tmp_path: Path):
    # Only run scale=10; tiers 50 and 250 must be NOT_MEASURED with a reason.
    kg = [{"scale": 10, "rubric": {"score": 0.5}, "graph_cleanliness": {"score": 0.9}}]
    out = render_report(kg, [], out_path=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tiers"]["10"]["status"] == "measured"
    for missing in ("50", "250"):
        assert data["tiers"][missing]["status"] == "NOT_MEASURED"
        assert "reason" in data["tiers"][missing]
        assert "NOT MEASURED" in data["tiers"][missing]["reason"].upper()


def test_render_report_cost_null_with_reason(tmp_path: Path):
    out = render_report([], [], out_path=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["cost"] is None
    assert "token" in data["cost_reason"].lower() or "cost" in data["cost_reason"].lower()


def test_render_report_serialises_run_result_dataclasses(corpus_dir: Path, project_dir: Path):
    kg = run_kg(corpus_dir, project_dir, scale=10)
    baseline = run_baseline(corpus_dir, query="Paris")
    out = render_report([kg], [baseline], out_path=project_dir.parent / "report-out")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tiers"]["10"]["status"] == "measured"
    assert data["kg_results"][0]["scale"] == 10
    assert data["baseline_results"][0]["query"] == "Paris"


# --- integration: 10-doc tier fully measured --------------------------------

def test_ten_doc_tier_fully_measured(corpus_dir: Path, project_dir: Path):
    """Per preflight honesty rules: 10-doc tier must produce real metrics."""
    result = run_kg(corpus_dir, project_dir, scale=10)
    assert result.graph_cleanliness["score"] is not None
    assert result.speed["median_seconds"] > 0.0
    assert result.rubric["score"] is not None
    assert result.cost is None  # cost legitimately null at this tier
