"""M2 E2E acceptance: pending review, wiki, WAL-safe snapshot recovery."""
import json
from pathlib import Path

from typer.testing import CliRunner

from kg.cli.main import app
from kg.embed import FakeEmbedder
from kg.ontology import Edge, Node
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter


def _assert_cli(result):
    assert result.exit_code == 0, f"exception={result.exception!r}\noutput={result.output}"


def _fake_embedders(monkeypatch):
    fake = lambda _: FakeEmbedder()
    for module in (
        "kg.cli.save.make_embedder",
        "kg.cli.query.make_embedder",
        "kg.cli.resolve_cli.make_embedder",
        "kg.cli.review.make_embedder",
    ):
        monkeypatch.setattr(module, fake)


def _state(adapter):
    nodes = {
        row["id"]: json.loads(row["data"])
        for row in adapter.conn.execute("SELECT id, data FROM nodes ORDER BY id")
    }
    edges = {
        row["id"]: json.loads(row["data"])
        for row in adapter.conn.execute("SELECT id, data FROM edges ORDER BY id")
    }
    return {"count": adapter.count(), "nodes": nodes, "edges": edges}


def _seed_pending(paths):
    adapter = SQLiteAdapter(paths.kg_db)
    winner = Node(
        id="u:person:winner", type="person", name="Winner", summary="short",
        attributes={"winner": True}, sources=[{"doc": "raw/winner.md", "chunk": "0"}],
    )
    loser = Node(
        id="u:person:loser", type="person", name="Loser", summary="longer enrichment",
        attributes={"loser": True}, sources=[{"doc": "raw/loser.md", "chunk": "1"}],
    )
    adapter.upsert_nodes([winner, loser])
    pending = Edge(
        id=f"{winner.id}|same_as|{loser.id}", semantic_type="same_as",
        status="pending", confidence=0.89,
    )
    incident = Edge(
        id=f"{loser.id}|related_to|u:person:outside", semantic_type="related_to",
        sources=[{"doc": "raw/edge.md"}],
    )
    adapter.upsert_edges([pending, incident])
    return adapter, winner, loser, pending, incident


def _delete_database(paths):
    for path in (paths.kg_db, Path(f"{paths.kg_db}-wal"), Path(f"{paths.kg_db}-shm")):
        path.unlink(missing_ok=True)


def test_m2_pending_confirm_wiki_wal_snapshot_restore(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    runner = CliRunner()
    _assert_cli(runner.invoke(app, ["init", "--user-id", "u", "--scope", "acceptance"]))
    paths = KgPaths.for_cwd()
    adapter, winner, loser, pending, incident = _seed_pending(paths)

    candidates = runner.invoke(app, ["dream", "candidates", "--kind", "pending", "--json"])
    _assert_cli(candidates)
    assert json.loads(candidates.output) == [{
        "reason": "pending", "node_ids": [winner.id, loser.id], "edge_id": pending.id,
        "score": 0.89, "detail": "gray-zone same_as", "node_type": None,
    }]

    confirmed = runner.invoke(app, [
        "review", "confirm", pending.id, "--winner", winner.id, "--reason", "acceptance",
    ])
    _assert_cli(confirmed)
    adapter = SQLiteAdapter(paths.kg_db)
    merged_winner, merged_loser = adapter.get(winner.id), adapter.get(loser.id)
    assert merged_winner.status == "active"
    assert merged_winner.summary == loser.summary
    assert merged_winner.attributes == {"winner": True, "loser": True}
    assert merged_loser.status == "tombstoned"
    assert merged_loser.merged_into == winner.id
    repointed_id = f"{winner.id}|related_to|u:person:outside"
    assert adapter.conn.execute("SELECT 1 FROM edges WHERE id=?", (repointed_id,)).fetchone()
    assert adapter.conn.execute("SELECT 1 FROM edges WHERE id=?", (pending.id,)).fetchone() is None
    audit_lines = paths.wiki.joinpath("log.md").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    audit = json.loads(audit_lines[0])
    assert audit["action"] == "review_confirm"
    assert audit["reason"] == "acceptance"
    assert audit["winner_before"] == winner.model_dump(mode="json")
    assert audit["loser_before"] == loser.model_dump(mode="json")

    synced = runner.invoke(app, ["wiki", "sync"])
    _assert_cli(synced)
    pages = list(paths.wiki.joinpath("entities").glob("*.md"))
    assert len(pages) == 1
    page = pages[0].read_text(encoding="utf-8")
    assert winner.id in page
    assert "Loser" in page
    assert "raw/winner.md" in page and "raw/loser.md" in page
    assert not any("loser--" in path.name for path in pages)

    expected = _state(adapter)
    artifact = tmp_path / "external-snapshot.zst"
    snap = runner.invoke(app, ["snapshot", "--output", str(artifact)])
    _assert_cli(snap)
    assert artifact.is_file() and artifact.stat().st_size > 0
    adapter.conn.close()
    _delete_database(paths)

    restored = runner.invoke(app, ["init", "--from-snapshot", str(artifact), "--force"])
    _assert_cli(restored)
    restored_adapter = SQLiteAdapter(paths.kg_db)
    assert _state(restored_adapter) == expected
    assert restored_adapter.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    restored_adapter.conn.close()


def test_m2_reject_snapshot_restore_preserves_rejected_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    runner = CliRunner()
    _assert_cli(runner.invoke(app, ["init", "--user-id", "u", "--scope", "acceptance"]))
    paths = KgPaths.for_cwd()
    adapter, winner, loser, pending, _ = _seed_pending(paths)

    rejected = runner.invoke(app, ["review", "reject", pending.id, "--reason", "distinct"])
    _assert_cli(rejected)
    row = adapter.conn.execute("SELECT status, data FROM edges WHERE id=?", (pending.id,)).fetchone()
    assert adapter.get(winner.id).status == adapter.get(loser.id).status == "active"
    assert row["status"] == json.loads(row["data"])["status"] == "rejected"
    expected = _state(adapter)
    artifact = tmp_path / "external-rejected.zst"
    snap = runner.invoke(app, ["snapshot", "--output", str(artifact)])
    _assert_cli(snap)
    adapter.conn.close()
    _delete_database(paths)

    restored = runner.invoke(app, ["init", "--from-snapshot", str(artifact), "--force"])
    _assert_cli(restored)
    restored_adapter = SQLiteAdapter(paths.kg_db)
    assert _state(restored_adapter) == expected
    row = restored_adapter.conn.execute("SELECT status, data FROM edges WHERE id=?", (pending.id,)).fetchone()
    assert row["status"] == json.loads(row["data"])["status"] == "rejected"
    assert restored_adapter.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    restored_adapter.conn.close()
