"""Tests for kg bench CLI (M6 T5)."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kg.cli.main import app

runner = CliRunner()


def test_bench_cli_scale10_exits_zero_and_writes_report(tmp_path):
    """`kg bench --scale 10` exits 0 and writes a real measured report.json."""
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["bench", "--scale", "10", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception!r}"

    report_path = out_dir / "report.json"
    assert report_path.exists(), f"report.json not written; output: {result.output}"

    payload = json.loads(report_path.read_text(encoding="utf-8"))

    # 10-doc tier is measured
    tier_10 = payload["tiers"]["10"]
    assert tier_10["status"] == "measured"
    assert "result" in tier_10

    # Scale matches
    kg_results = payload["kg_results"]
    assert any(r["scale"] == 10 for r in kg_results)

    # Honesty: 50 and 250 not run, explicitly NOT_MEASURED (not fabricated)
    for s in ("50", "250"):
        tier = payload["tiers"][s]
        assert tier["status"] == "NOT_MEASURED", f"scale {s} should be NOT_MEASURED"
        assert "result" not in tier


def test_bench_cli_default_out_dir(tmp_path, monkeypatch):
    """Without --out-dir, results default to bench/results/<date>/."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["bench", "--scale", "10"])
    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception!r}"
    results_root = tmp_path / "bench" / "results"
    assert results_root.is_dir()
    subdirs = [p for p in results_root.iterdir() if p.is_dir()]
    assert subdirs, f"no date dir under {results_root}"
    assert any((d / "report.json").exists() for d in subdirs)


def test_bench_cli_invalid_scale_rejected(tmp_path):
    """--scale 7 is rejected with non-zero exit."""
    result = runner.invoke(app, ["bench", "--scale", "7", "--out-dir", str(tmp_path)])
    assert result.exit_code != 0
    combined = (result.output or "") + (result.stderr or "")
    assert "10, 50, or 250" in combined or "10, 50, 250" in combined, \
        f"validation message missing; output: {result.output}"


def test_bench_cli_scale50_runs_and_marks_10_250_not_measured(tmp_path):
    """`kg bench --scale 50` runs the 50-doc tier; 10/250 remain NOT_MEASURED.

    May be slow (~5x the 10-doc run). Real, not skipped.
    """
    out_dir = tmp_path / "out50"
    result = runner.invoke(app, ["bench", "--scale", "50", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, f"output: {result.output}\nexc: {result.exception!r}"
    payload = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["tiers"]["50"]["status"] == "measured"
    assert payload["tiers"]["10"]["status"] == "NOT_MEASURED"
    assert payload["tiers"]["250"]["status"] == "NOT_MEASURED"


def test_bench_yml_no_llm_network_deps():
    """bench.yml must not reference ANTHROPIC_API_KEY or live-session gates."""
    bench_yml = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "bench.yml"
    assert bench_yml.exists(), "bench.yml not found"
    text = bench_yml.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text, "bench.yml should not need API key"
    assert "live-session" not in text
    assert "command -v claude" not in text
    assert "make bench" in text or "uv run kg bench" in text


def test_makefile_has_bench_target():
    Makefile = Path(__file__).resolve().parents[2] / "Makefile"
    text = Makefile.read_text(encoding="utf-8")
    assert "bench:" in text
    assert "uv run kg bench --scale 10" in text
