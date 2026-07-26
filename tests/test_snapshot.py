import sqlite3

import pytest
from typer.testing import CliRunner

from kg.cli.init import init_project
from kg.cli.main import app
from kg.ontology import Edge, Node
from kg.snapshot import create_snapshot, restore_snapshot
from kg.storage.sqlite import SQLiteAdapter


def _edge(source: str, target: str) -> Edge:
    return Edge(id=f"{source}|related_to|{target}", semantic_type="related_to")


def test_wal_snapshot_captures_committed_row(tmp_path):
    source = tmp_path / "source.db"
    conn = sqlite3.connect(source)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE records(value TEXT)")
    conn.execute("INSERT INTO records VALUES ('wal-state')")
    conn.commit()
    assert (tmp_path / "source.db-wal").exists()

    artifact = create_snapshot(source, tmp_path / "snapshot.zst")
    restored = tmp_path / "restored.db"
    restore_snapshot(artifact, restored)
    assert sqlite3.connect(restored).execute("SELECT value FROM records").fetchone()[0] == "wal-state"
    conn.close()


def test_roundtrip_graph_and_recovery_state(tmp_path):
    paths = init_project(tmp_path / "project", user_id="u", scope="s")
    db = SQLiteAdapter(paths.kg_db)
    winner = Node(id="u:person:winner", type="person", name="Winner", canonical_name="Winner")
    loser = Node(id="u:person:loser", type="person", name="Loser", status="tombstoned", merged_into=winner.id)
    other = Node(id="u:person:other", type="person", name="Other")
    db.upsert_nodes([winner, loser, other])
    db.upsert_edges([_edge(winner.id, other.id)])
    artifact = create_snapshot(paths.kg_db, tmp_path / "portable.zst")
    db.conn.close()

    for suffix in ("", "-wal", "-shm"):
        (tmp_path / "project" / ".kg" / f"kg.db{suffix}").unlink(missing_ok=True)
    restore_snapshot(artifact, paths.kg_db)

    restored = SQLiteAdapter(paths.kg_db)
    assert restored.get(winner.id).canonical_name == "Winner"
    assert restored.get(loser.id).status == "tombstoned"
    assert restored.get(loser.id).merged_into == winner.id
    assert restored.neighbors([winner.id]).edges[0].id == f"{winner.id}|related_to|{other.id}"
    restored.conn.close()


def test_restore_refuses_overwrite_and_corrupt_artifact_preserves_destination(tmp_path):
    destination = tmp_path / "db.sqlite"
    destination.write_bytes(b"original")
    artifact = tmp_path / "bad.zst"
    artifact.write_bytes(b"not zstd")

    with pytest.raises(FileExistsError):
        restore_snapshot(artifact, destination)
    assert destination.read_bytes() == b"original"
    with pytest.raises(Exception):
        restore_snapshot(artifact, destination, force=True)
    assert destination.read_bytes() == b"original"


def test_restore_removes_stale_sidecars_only_after_success_and_honors_size_limit(tmp_path):
    source = tmp_path / "source.db"
    sqlite3.connect(source).execute("CREATE TABLE data(value TEXT)").connection.close()
    artifact = create_snapshot(source, tmp_path / "source.zst")
    destination = tmp_path / "destination.db"
    destination.write_bytes(b"old")
    wal, shm = tmp_path / "destination.db-wal", tmp_path / "destination.db-shm"
    wal.write_bytes(b"wal")
    shm.write_bytes(b"shm")

    with pytest.raises(ValueError):
        restore_snapshot(artifact, destination, force=True, max_bytes=1)
    assert destination.read_bytes() == b"old"
    assert wal.exists() and shm.exists()

    restore_snapshot(artifact, destination, force=True)
    assert not wal.exists() and not shm.exists()


def test_init_from_external_snapshot(tmp_path, monkeypatch):
    source_paths = init_project(tmp_path / "source", user_id="u", scope="s")
    db = SQLiteAdapter(source_paths.kg_db)
    node = Node(id="u:person:x", type="person", name="X")
    db.upsert_nodes([node])
    artifact = create_snapshot(source_paths.kg_db, tmp_path / "external.zst")
    db.conn.close()

    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.chdir(target)
    result = CliRunner().invoke(app, ["init", "--from-snapshot", str(artifact)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    restored = SQLiteAdapter(target / ".kg" / "kg.db")
    assert restored.get(node.id) is not None
    restored.conn.close()
