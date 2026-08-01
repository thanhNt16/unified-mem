"""CBM adoption regression gate."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class EvaluationResult:
    ok: bool
    failures: tuple[str, ...]


FLOORS = {
    "recall_at_5": 0.90,
    "recall_at_10": 0.95,
    "mrr": 0.85,
}
CEILINGS = {
    "build_seconds": 10.0,
    "search_ms": 50.0,
    "query_ms": 200.0,
    "graph_hot_ms": 500.0,
}


def evaluate(metrics: dict[str, float]) -> EvaluationResult:
    failures = [key for key, floor in FLOORS.items() if metrics.get(key, 0.0) < floor]
    failures.extend(
        key for key, ceiling in CEILINGS.items()
        if metrics.get(key, float("inf")) > ceiling
    )
    return EvaluationResult(not failures, tuple(failures))


def parse_scale_100k(output: str) -> dict[str, float]:
    """Parse the stable machine-readable values emitted by scale_100k.py."""
    patterns = {
        "build_seconds": r"build: nodes ([0-9.]+)s",
        "search_ms": r"search: ([0-9.]+)ms",
        "query_ms": r"query \(find\): ([0-9.]+)ms",
        "graph_hot_ms": r"/graph\.json: ([0-9.]+)ms",
    }
    values: dict[str, float] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if match is None:
            raise ValueError(f"missing {key} in scale benchmark output")
        values[key] = float(match.group(1))
    return values


def benchmark_metrics(report: Path) -> dict[str, float]:
    payload = json.loads(report.read_text(encoding="utf-8"))
    aggregate = payload["recall"]["aggregate"]
    return {
        "recall_at_5": float(aggregate["recall_at_5"]),
        "recall_at_10": float(aggregate["recall_at_10"]),
        "mrr": float(aggregate["mrr"]),
    }


def _run(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    output = completed.stdout + completed.stderr
    if completed.returncode:
        raise RuntimeError(f"{' '.join(command)} failed:\n{output}")
    return output


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = root / "bench" / "results" / stamp

    # Do not create the timestamp directory before pytest: acceptance tests
    # intentionally inspect the latest result directory for report.json.
    pytest_output = _run(["uv", "run", "pytest", "tests/", "-x"], root)
    bench_output = _run(
        ["uv", "run", "kg", "bench", "--scale", "10", "--dimension", "all", "--out-dir", str(out)], root
    )
    scale_10k_output = _run(["uv", "run", "pytest", "tests/test_scale_10k.py", "-v", "-s"], root)
    scale_100k_output = _run(["uv", "run", "python", "bench/scale_100k.py"], root)

    metrics = benchmark_metrics(out / "report.json")
    metrics.update(parse_scale_100k(scale_100k_output))
    result = evaluate(metrics)
    artifacts = {
        "metrics": metrics,
        "result": asdict(result),
        "commands": {
            "pytest.txt": pytest_output,
            "bench.txt": bench_output,
            "scale-10k.txt": scale_10k_output,
            "scale-100k.txt": scale_100k_output,
        },
    }
    (out / "evaluation.json").write_text(json.dumps(artifacts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, content in artifacts["commands"].items():
        (out / name).write_text(content, encoding="utf-8")
    if result.ok:
        print(f"CBM evaluation passed: {out}")
        return 0
    print(f"CBM evaluation failed ({', '.join(result.failures)}): {out}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
