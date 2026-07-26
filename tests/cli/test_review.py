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
    winner = Node(id="u:person:w", type="person", name="Win", summary='x\n# "quoted"')
    loser = Node(id="u:person:l", type="person", name="Lose", summary="longer")
    adapter.upsert_nodes([winner, loser])
    review = Edge(
        id=f"{winner.id}|same_as|{loser.id}", semantic_type="same_as",
        status="pending", confidence=0.9,
    )
    adapter.upsert_edges([review])
    return paths, adapter, winner, loser, review


def _audit(paths):
    lines = paths.wiki.joinpath("log.md").read_text().splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_merge_first_argument_wins_and_writes_injection_safe_audit(tmp_path, monkeypatch):
    paths, adapter, winner, loser, review = _project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["merge", winner.id, loser.id, "--reason", 'why\n# "x"'])

    assert result.exit_code == 0, result.output
    adapter = SQLiteAdapter(paths.kg_db)
    assert adapter.get(winner.id).status == "active"
    assert adapter.get(loser.id).merged_into == winner.id
    audit = _audit(paths)
    assert audit["action"] == "merge"
    assert audit["reason"] == 'why\n# "x"'
    assert audit["winner_before"] == winner.model_dump(mode="json")
    assert audit["loser_before"] == loser.model_dump(mode="json")


def test_review_confirm_requires_explicit_endpoint_winner_and_consumes_edge(tmp_path, monkeypatch):
    paths, adapter, winner, loser, review = _project(tmp_path, monkeypatch)
    result = runner.invoke(app, [
        "review", "confirm", review.id, "--winner", loser.id, "--reason", "preferred",
    ])

    assert result.exit_code == 0, result.output
    adapter = SQLiteAdapter(paths.kg_db)
    assert adapter.get(loser.id).status == "active"
    assert adapter.get(winner.id).merged_into == loser.id
    assert adapter.conn.execute("SELECT 1 FROM edges WHERE id=?", (review.id,)).fetchone() is None
    audit = _audit(paths)
    assert audit["action"] == "review_confirm"
    assert audit["winner_id"] == loser.id
    assert audit["loser_id"] == winner.id
    assert audit["review_edge_id"] == review.id


def test_review_reject_keeps_nodes_and_audits(tmp_path, monkeypatch):
    paths, adapter, winner, loser, review = _project(tmp_path, monkeypatch)
    result = runner.invoke(app, ["review", "reject", review.id, "--reason", "distinct"])

    assert result.exit_code == 0, result.output
    adapter = SQLiteAdapter(paths.kg_db)
    assert adapter.get(winner.id).status == adapter.get(loser.id).status == "active"
    row = adapter.conn.execute("SELECT status, data FROM edges WHERE id=?", (review.id,)).fetchone()
    assert row["status"] == Edge.model_validate_json(row["data"]).status == "rejected"
    assert _audit(paths)["reason"] == "distinct"


def test_review_list_only_pending_and_read_only(tmp_path, monkeypatch):
    paths, adapter, winner, loser, review = _project(tmp_path, monkeypatch)
    rejected = Edge(
        id=f"{loser.id}|same_as|{winner.id}", semantic_type="same_as",
        status="rejected", confidence=0.2,
    )
    adapter.upsert_edges([rejected])
    before = paths.kg_db.stat().st_mtime_ns

    result = runner.invoke(app, ["review", "list"])

    assert result.exit_code == 0, result.output
    assert review.id in result.output
    assert rejected.id not in result.output
    assert paths.kg_db.stat().st_mtime_ns == before


def test_audit_failure_warns_after_committed_merge(tmp_path, monkeypatch):
    paths, adapter, winner, loser, review = _project(tmp_path, monkeypatch)
    paths.wiki.joinpath("log.md").mkdir()

    result = runner.invoke(app, ["merge", winner.id, loser.id])

    assert result.exit_code == 0
    assert "warning" in result.output.lower()
    adapter = SQLiteAdapter(paths.kg_db)
    assert adapter.get(loser.id).merged_into == winner.id
