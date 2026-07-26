import json

from typer.testing import CliRunner

from kg.cli.main import app
from kg.embed import FakeEmbedder


def _fake_embedders(monkeypatch):
    monkeypatch.setattr("kg.cli.save.make_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr("kg.cli.query.make_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr("kg.cli.resolve_cli.make_embedder", lambda _: FakeEmbedder())


def test_kg_save_persists_and_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--user-id", "u", "--scope", "s"]).exit_code == 0
    (tmp_path / "n.json").write_text(json.dumps([
        {"type": "person", "name": "Demis Hassabis", "summary": "founder"}
    ]))
    (tmp_path / "e.json").write_text("[]")

    result = runner.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                                 "--edges", str(tmp_path / "e.json"),
                                 "--source", "raw/x.md#chunk-0"])

    assert result.exit_code == 0, result.stdout
    assert "NEW" in result.stdout or "RESOLVED" in result.stdout


def test_resolve_and_dedup_check_do_not_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    runner = CliRunner()
    runner.invoke(app, ["init", "--user-id", "u"])

    resolve = runner.invoke(app, ["resolve", "Unknown", "--type", "person"])
    dedup = runner.invoke(app, ["dedup-check", '{"type":"person","name":"Unknown"}'])

    assert resolve.exit_code == dedup.exit_code == 0
    assert "none" in resolve.stdout
    assert "best=None" in dedup.stdout


def test_kg_save_supports_facts_and_preferences(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--user-id", "u", "--scope", "s"]).exit_code == 0
    (tmp_path / "n.json").write_text("[]")
    (tmp_path / "e.json").write_text("[]")
    (tmp_path / "f.json").write_text(json.dumps([
        {"subject": "AlphaFold 3", "predicate": "released_in", "object": "2024"}
    ]))
    (tmp_path / "p.json").write_text(json.dumps([
        {"name": "theme", "attributes": {"value": "dark"}}
    ]))

    result = runner.invoke(app, [
        "save",
        "--nodes", str(tmp_path / "n.json"),
        "--edges", str(tmp_path / "e.json"),
        "--source", "raw/x.md#chunk-0",
        "--facts", str(tmp_path / "f.json"),
        "--preferences", str(tmp_path / "p.json"),
    ])

    assert result.exit_code == 0, result.stdout
    assert "fact" in result.stdout
    assert "preference" in result.stdout


def test_kg_save_reports_dropped_edges(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    runner = CliRunner()
    runner.invoke(app, ["init", "--user-id", "u"])
    (tmp_path / "n.json").write_text(json.dumps([
        {"type": "person", "name": "Alice"}
    ]))
    (tmp_path / "e.json").write_text(json.dumps([
        {"source_name": "Alice", "semantic_type": "employed_by",
         "target_name": "Ghost Org"}
    ]))

    result = runner.invoke(app, [
        "save",
        "--nodes", str(tmp_path / "n.json"),
        "--edges", str(tmp_path / "e.json"),
        "--source", "raw/x.md#chunk-0",
    ])

    assert result.exit_code == 0, result.stdout
    assert "dropped edges: 1" in result.stdout
