import json
from typer.testing import CliRunner

from kg.cli.init import init_project
from kg.cli.main import app


def test_index_scan_skips_unchanged_source_and_writes_coverage(tmp_path):
    init_project(tmp_path, user_id="u", scope="p")
    (tmp_path / "a.py").write_text("def f(): pass\n")
    runner = CliRunner()
    first = runner.invoke(app, ["index", "scan", str(tmp_path), "--mode", "fast"])
    assert first.exit_code == 0, first.output
    assert "indexed: 1" in first.output
    second = runner.invoke(app, ["index", "scan", str(tmp_path), "--mode", "fast"])
    assert second.exit_code == 0, second.output
    assert "skipped: 1" in second.output
    coverage = json.loads((tmp_path / ".kg" / "coverage.json").read_text())
    assert coverage["sources"]["skipped"] == 1


def test_index_rebuild_full_accepts_flag(tmp_path):
    init_project(tmp_path, user_id="u", scope="p")
    result = CliRunner().invoke(app, ["index", "rebuild", str(tmp_path), "--full"])
    assert result.exit_code == 0, result.output
    assert "Rebuild published." in result.output


def test_index_status_reads_coverage(tmp_path):
    init_project(tmp_path, user_id="u", scope="p")
    (tmp_path / "a.py").write_text("def f(): pass\n")
    runner = CliRunner()
    assert runner.invoke(app, ["index", "scan", str(tmp_path), "--mode", "fast"]).exit_code == 0
    result = runner.invoke(app, ["index", "status", str(tmp_path)])
    assert result.exit_code == 0
    assert "indexed: 1" in result.output
