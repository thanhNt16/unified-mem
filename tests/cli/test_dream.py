import json

from typer.testing import CliRunner

from kg.cli.main import app
from kg.ontology import Edge, Node
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter

runner = CliRunner()


def _project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--user-id", "u", "--scope", "s"]).exit_code == 0
    paths = KgPaths.for_cwd()
    adapter = SQLiteAdapter(paths.kg_db)
    return paths, adapter


def test_dream_candidates_runs_empty(tmp_path, monkeypatch):
    paths, adapter = _project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["dream", "candidates"])
    assert result.exit_code == 0
    assert "no candidates" in result.output


def test_dream_candidates_no_db_write(tmp_path, monkeypatch):
    paths, adapter = _project(tmp_path, monkeypatch)
    before = paths.kg_db.stat().st_mtime_ns
    result = runner.invoke(app, ["dream", "candidates"])
    assert result.exit_code == 0
    assert paths.kg_db.stat().st_mtime_ns == before


def test_dream_candidates_pending(tmp_path, monkeypatch):
    paths, adapter = _project(tmp_path, monkeypatch)
    a = Node(id="u:person:a", type="person", name="Alice", created_at="2026-07-01T00:00:00Z")
    b = Node(id="u:person:b", type="person", name="Alicia", created_at="2026-07-01T00:00:00Z")
    adapter.upsert_nodes([a, b])
    edge = Edge(
        id="u:person:a|same_as|u:person:b",
        semantic_type="same_as", source="u:person:a", target="u:person:b",
        status="pending", confidence=0.89,
    )
    adapter.upsert_edges([edge])
    before = paths.kg_db.stat().st_mtime_ns
    result = runner.invoke(app, ["dream", "candidates"])
    assert result.exit_code == 0, result.output
    assert "pending" in result.output
    assert edge.id in result.output
    assert paths.kg_db.stat().st_mtime_ns == before


def test_dream_candidates_json(tmp_path, monkeypatch):
    paths, adapter = _project(tmp_path, monkeypatch)
    a = Node(id="u:person:a", type="person", name="Alice", created_at="2026-07-01T00:00:00Z")
    adapter.upsert_nodes([a])
    result = runner.invoke(app, ["dream", "candidates", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)


def test_dream_candidates_json_deterministic(tmp_path, monkeypatch):
    paths, adapter = _project(tmp_path, monkeypatch)
    a = Node(id="u:person:a", type="person", name="Alice", created_at="2026-07-01T00:00:00Z")
    b = Node(id="u:person:b", type="person", name="Alicia", created_at="2026-07-01T00:00:00Z")
    adapter.upsert_nodes([a, b])
    adapter.upsert_edges([Edge(
        id="u:person:a|same_as|u:person:b",
        semantic_type="same_as", source="u:person:a", target="u:person:b",
        status="pending", confidence=0.89,
    )])
    r1 = runner.invoke(app, ["dream", "candidates", "--json"])
    r2 = runner.invoke(app, ["dream", "candidates", "--json"])
    assert json.loads(r1.output) == json.loads(r2.output)


def test_dream_candidates_kind_filter(tmp_path, monkeypatch):
    paths, adapter = _project(tmp_path, monkeypatch)
    a = Node(id="u:person:a", type="person", name="Alice", created_at="2026-07-01T00:00:00Z")
    b = Node(id="u:person:b", type="person", name="Alicia", created_at="2026-07-01T00:00:00Z")
    adapter.upsert_nodes([a, b])
    adapter.upsert_edges([Edge(
        id="u:person:a|same_as|u:person:b",
        semantic_type="same_as", source="u:person:a", target="u:person:b",
        status="pending", confidence=0.89,
    )])
    result = runner.invoke(app, ["dream", "candidates", "--kind", "orphan"])
    assert result.exit_code == 0
    assert "orphan" in result.output
    assert "pending" not in result.output
    assert "same_as" not in result.output


def test_dream_candidates_kind_filter_pending(tmp_path, monkeypatch):
    paths, adapter = _project(tmp_path, monkeypatch)
    a = Node(id="u:person:a", type="person", name="Alice", created_at="2026-07-01T00:00:00Z")
    b = Node(id="u:person:b", type="person", name="Alicia", created_at="2026-07-01T00:00:00Z")
    adapter.upsert_nodes([a, b])
    adapter.upsert_edges([Edge(
        id="u:person:a|same_as|u:person:b",
        semantic_type="same_as", source="u:person:a", target="u:person:b",
        status="pending", confidence=0.89,
    )])
    result = runner.invoke(app, ["dream", "candidates", "--kind", "PENDING"])
    assert result.exit_code == 0
    assert "pending" in result.output


def test_dream_candidates_since(tmp_path, monkeypatch):
    paths, adapter = _project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["dream", "candidates", "--since", "7d"])
    assert result.exit_code == 0
