import json

from typer.testing import CliRunner

from kg.cli.main import app
from kg.embed import FakeEmbedder
from kg.ids import node_id


def _fake_embedders(monkeypatch):
    monkeypatch.setattr("kg.cli.save.make_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr("kg.cli.query.make_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr("kg.cli.resolve_cli.make_embedder", lambda _: FakeEmbedder())


def _save(runner, tmp_path):
    (tmp_path / "n.json").write_text(json.dumps([
        {"type": "person", "name": "Alice", "summary": "DeepMind founder"},
        {"type": "organization", "name": "DeepMind"},
    ]))
    (tmp_path / "e.json").write_text(json.dumps([
        {"source_name": "Alice", "semantic_type": "employed_by", "target_name": "DeepMind"}
    ]))
    return runner.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                               "--edges", str(tmp_path / "e.json"),
                               "--source", "raw/x.md#chunk-0"])


def test_kg_search_after_save(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    runner = CliRunner()
    runner.invoke(app, ["init", "--user-id", "u", "--scope", "s"])
    assert _save(runner, tmp_path).exit_code == 0

    out = runner.invoke(app, ["search", "DeepMind founder"])

    assert out.exit_code == 0, out.stdout
    assert "u:person:alice" in out.stdout


def test_kg_expand_and_pack(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    runner = CliRunner()
    runner.invoke(app, ["init", "--user-id", "u"])
    assert _save(runner, tmp_path).exit_code == 0
    alice = node_id("u", "person", "Alice")

    expand = runner.invoke(app, ["expand", alice])
    packed = runner.invoke(app, ["pack"], input=json.dumps({
        "nodes": [{"id": alice, "type": "person", "name": "Alice"}], "edges": []
    }))

    assert expand.exit_code == packed.exit_code == 0
    assert "nodes: 2" in expand.stdout and "edges: 1" in expand.stdout
    assert "# kg context" in packed.stdout
