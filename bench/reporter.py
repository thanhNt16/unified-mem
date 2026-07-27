"""Honest benchmark report rendering."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _serialise(value: Any) -> Any:
    return value.to_dict() if hasattr(value, "to_dict") else value


def render_report(kg_results, baseline_results, *, out_path) -> Path:
    """Write structured JSON plus markdown; omitted tiers remain explicitly null."""
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
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return json_path
