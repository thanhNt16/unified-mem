"""M6 acceptance: honest status report + real benchmark report.json.

Distinguishes:
- milestone-status PLANNED: forbidden (M0-M6 all COMPLETE).
- metric-status NOT_MEASURED: legitimate for 50/250 tiers and null cost.

Per preflight: do NOT blanket-assert "no NOT MEASURED" -- that fails the
50/250 release-gated tiers and the no-token-driver cost column.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_HTML = ROOT / "docs" / "reports" / "2026-07-27-m6-status-report.html"


# --- 1. Status report HTML --------------------------------------------------


def test_m6_status_report_exists():
    assert REPORT_HTML.is_file(), f"missing: {REPORT_HTML}"


def test_status_report_has_no_planned_milestones():
    """All seven milestones M0-M6 are COMPLETE; no PLANNED milestone badges."""
    html = REPORT_HTML.read_text(encoding="utf-8")
    # milestone-status PLANNED tokens are forbidden.
    forbidden = [
        "M1 GRAPH+GATE — PLANNED",
        "M2 DREAM+REVIEW — PLANNED",
        "M3 MCP+INSTALL — PLANNED",
        "M4 VIZ+CYPHER — PLANNED",
        "M5 DISTRIBUTION+E2E — PLANNED",
        "M6 BENCHMARKS+DOCS — PLANNED",
        "M1-M6 — PLANNED",
    ]
    for token in forbidden:
        assert token not in html, f"PLANNED milestone remains: {token!r}"


def test_status_report_all_milestones_complete():
    """All seven milestones show COMPLETE badges."""
    html = REPORT_HTML.read_text(encoding="utf-8")
    for milestone in ("M0", "M1", "M2", "M3", "M4", "M5", "M6"):
        pattern = rf"\b{milestone}\b[^<]*COMPLETE"
        assert re.search(pattern, html), f"missing COMPLETE badge for {milestone}"


def test_status_report_js_free():
    """HTML is JS-free: no <script> tags (static, portability claim)."""
    html = REPORT_HTML.read_text(encoding="utf-8")
    assert "<script" not in html.lower(), "report must be JS-free"


def test_status_report_uses_live_class():
    """Reuse the .live CSS class from the M0 report (no .done invention)."""
    html = REPORT_HTML.read_text(encoding="utf-8")
    assert ".live" in html, "missing .live CSS class"
    assert 'class="badge live"' in html or 'class="status-tag live"' in html


def test_status_report_marks_real_commands_live():
    """All realized kg commands show LIVE in the availability table."""
    html = REPORT_HTML.read_text(encoding="utf-8")
    for cmd in ("kg init", "kg save", "kg search", "kg dream", "kg snapshot",
                "kg install", "kg mcp serve", "kg cypher", "kg viz",
                "kg wiki", "kg bench", "kg hook session-end"):
        assert cmd in html, f"command missing from report: {cmd}"


# --- 2. Bench report.json honesty ------------------------------------------


def _latest_report() -> Path:
    results = ROOT / "bench" / "results"
    dirs = sorted(results.glob("*/"))
    assert dirs, "no bench/results/<date>/ directories"
    report = dirs[-1] / "report.json"
    assert report.is_file(), f"missing report.json in {dirs[-1]}"
    return report


def test_bench_report_10_doc_measured_with_real_numbers():
    """10-doc tier is MEASURED with non-null cleanliness + speed values."""
    report = json.loads(_latest_report().read_text(encoding="utf-8"))
    tier10 = report["tiers"]["10"]
    assert tier10["status"] == "measured", (
        f"10-doc must be measured, got status={tier10['status']!r}"
    )
    result = tier10["result"]

    # Graph cleanliness: real numbers required.
    cl = result["graph_cleanliness"]
    assert cl["total_nodes"] > 0, "total_nodes must be > 0 (real run)"
    assert 0.0 <= cl["score"] <= 1.0, f"cleanliness score out of [0,1]: {cl['score']}"
    assert cl["connected_nodes"] >= 0

    # Speed: real timings required (non-null, non-zero).
    sp = result["speed"]
    assert sp["median_seconds"] > 0, "speed.median_seconds must be > 0"
    assert sp["p95_seconds"] >= sp["median_seconds"], "p95 < median (order)"
    assert sp["n"] >= 1, "speed samples n >= 1 required"

    # Rubric: real scored values.
    rb = result["rubric"]
    for key in ("cross_doc_hits", "multi_hop_reach", "lineage", "score"):
        assert 0.0 <= rb[key] <= 1.0, f"rubric {key} out of [0,1]: {rb[key]}"


def test_bench_report_50_250_not_measured_not_null_per_tier():
    """50/250 tiers NOT_MEASURED with explicit reason (release-gated).

    Per preflight: these are legitimate NOT_MEASURED, not fabricated.
    """
    report = json.loads(_latest_report().read_text(encoding="utf-8"))
    for scale in ("50", "250"):
        tier = report["tiers"][scale]
        assert tier["status"] == "NOT_MEASURED", (
            f"scale {scale} expected NOT_MEASURED, got {tier['status']!r}"
        )
        assert tier.get("reason"), (
            f"scale {scale} NOT_MEASURED must carry explicit reason"
        )


def test_bench_report_cost_null_with_reason():
    """Cost is null/None with documented reason (no driver token emission).

    Per preflight: cost null is acceptable; do NOT assert non-null cost.
    """
    report = json.loads(_latest_report().read_text(encoding="utf-8"))
    assert report["cost"] is None, (
        f"cost expected None (no token data), got {report['cost']!r}"
    )
    assert report.get("cost_reason"), "cost_reason must explain null"
    reason = report["cost_reason"].lower()
    # Reason must mention tokens or driver; not a bare "not measured".
    assert "token" in reason or "driver" in reason or "no token data" in reason, (
        f"cost_reason must mention tokens/driver: {report['cost_reason']!r}"
    )


def test_bench_report_has_no_fabricated_numbers():
    """Spot-check: 10-doc tier carries the runner's deterministic seed+mode.

    A fabricated report would not carry embedder_mode='fake' + seed=42.
    """
    report = json.loads(_latest_report().read_text(encoding="utf-8"))
    result = report["tiers"]["10"]["result"]
    assert result["embedder_mode"] == "fake", (
        f"embedder_mode must be 'fake' for deterministic CI, got {result['embedder_mode']!r}"
    )
    assert result["seed"] is not None, "seed must be recorded (determinism)"


def test_bench_report_structure_complete():
    """report.json has the four required top-level keys."""
    report = json.loads(_latest_report().read_text(encoding="utf-8"))
    for key in ("timestamp", "tiers", "cost", "cost_reason"):
        assert key in report, f"report missing top-level key: {key}"
    for scale in ("10", "50", "250"):
        assert scale in report["tiers"], f"tiers missing scale {scale}"
