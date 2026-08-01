"""Honest benchmark report rendering."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _serialise(value: Any) -> Any:
    return value.to_dict() if hasattr(value, "to_dict") else value


def render_report(
    kg_results,
    baseline_results,
    *,
    out_path,
    recall_result: dict | None = None,
    comparative_result: dict | None = None,
) -> Path:
    """Write structured JSON plus markdown; omitted tiers remain explicitly null.

    Args:
        kg_results: List of RunResult from run_kg.
        baseline_results: List of BaselineResult from run_baseline.
        out_path: Output directory (or file path pointing to dir).
        recall_result: Optional dict from run_recall_benchmark.
        comparative_result: Optional dict from run_comparative.

    Returns:
        Path to the written report.json.
    """
    target = Path(out_path)
    out_dir = target if target.suffix == "" else target.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    kg = [_serialise(result) for result in kg_results]
    baseline = [_serialise(result) for result in baseline_results]
    by_scale = {result["scale"]: result for result in kg}
    tiers = {}
    for scale in (10, 50, 250):
        if scale in by_scale:
            tiers[str(scale)] = {"status": "measured", "result": by_scale[scale]}
        else:
            tiers[str(scale)] = {"status": "NOT_MEASURED", "reason": "NOT MEASURED: tier not run"}
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kg_results": kg,
        "baseline_results": baseline,
        "tiers": tiers,
        "cost": None,
        "cost_reason": "No token data exists; cost is not measured.",
        "recall": recall_result,
        "comparative": _serialise(comparative_result) if comparative_result else None,
    }
    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Benchmark report",
        "",
        f"Timestamp: {payload['timestamp']}",
        "",
        "| Scale | Status |",
        "| --- | --- |",
    ]
    for scale, tier in tiers.items():
        lines.append(f"| {scale} | {tier['status']} |")
    lines.extend([
        "",
        f"Cost: null — {payload['cost_reason']}",
        "",
    ])

    if recall_result:
        agg = recall_result.get("aggregate", {})
        lines.extend([
            "## Recall@k (D3)",
            "",
            "| Metric | Value | Target |",
            "| --- | --- | --- |",
            f"| Recall@5 | {agg.get('recall_at_5', 'N/A'):.2f} | ≥0.90 |",
            f"| Recall@10 | {agg.get('recall_at_10', 'N/A'):.2f} | ≥0.95 |",
            f"| MRR | {agg.get('mrr', 'N/A'):.2f} | ≥0.85 |",
            "",
        ])

    if comparative_result:
        lines.extend([
            "## Comparative (D4)",
            "",
            "| Query | kg hits | grep hits | kg ms | grep ms |",
            "| --- | --- | --- | --- | --- |",
        ])
        for row in comparative_result.get("queries", []):
            lines.append(
                f"| {row['query']} | {row['kg_hit_count']} | {row['grep_hit_count']} | "
                f"{row['kg_ms']:.3f} | {row['grep_ms']:.3f} |"
            )
        lines.extend([
            "",
            f"**Totals:** kg={comparative_result.get('kg_total_hits', 0)} hits, "
            f"grep={comparative_result.get('grep_total_hits', 0)} hits",
            "",
        ])

    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return json_path
