import json

from kg.coverage import CoverageReport


def test_coverage_writes_machine_readable_counts(tmp_path):
    report = CoverageReport(generation=7, mode="fast")
    report.record("a.py", "indexed", outputs=["code:p:a.py"])
    report.record("broken.ts", "failed", reason="parse_error")
    report.write_atomic(tmp_path / "coverage.json")
    body = json.loads((tmp_path / "coverage.json").read_text())
    assert body["sources"] == {"seen": 2, "indexed": 1, "skipped": 0, "failed": 1, "stale": 0}
    assert body["by_reason"]["parse_error"] == 1
    assert body["outputs"] == {"nodes": 1, "edges": 0}
