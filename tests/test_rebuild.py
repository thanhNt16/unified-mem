import json

import pytest

from kg.cli.init import init_project
from kg.ontology import Node
from kg.rebuild import RebuildCandidate, WriterLock
from kg.storage.sqlite import SQLiteAdapter


def test_publish_replaces_live_db_only_after_integrity_validation(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="p")
    old = SQLiteAdapter(paths.kg_db)
    old.upsert_nodes([Node(id="u:object:old", type="object", name="old")])
    old.conn.close()

    candidate = RebuildCandidate.create(paths)
    staging = SQLiteAdapter(candidate.db_path)
    staging.upsert_nodes([Node(id="u:object:new", type="object", name="new")])
    staging.conn.close()
    candidate.publish()

    live = SQLiteAdapter(paths.kg_db)
    assert live.get("u:object:old") is None
    assert live.get("u:object:new") is not None
    live.conn.close()


def test_failed_candidate_never_replaces_live_db(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="p")
    live = SQLiteAdapter(paths.kg_db)
    live.upsert_nodes([Node(id="u:object:old", type="object", name="old")])
    live.conn.close()
    candidate = RebuildCandidate.create(paths)
    candidate.db_path.write_bytes(b"not a sqlite database")

    with pytest.raises(RuntimeError, match="integrity_check"):
        candidate.publish()

    live = SQLiteAdapter(paths.kg_db)
    assert live.get("u:object:old") is not None
    live.conn.close()
    assert candidate.db_path.exists()


def test_second_writer_lock_fails_without_stealing(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="p")
    first = WriterLock.acquire(paths.root)
    try:
        metadata = json.loads((paths.root / ".writer.lock" / "metadata.json").read_text())
        assert {"pid", "hostname", "created_at"} <= metadata.keys()
        with pytest.raises(RuntimeError, match="writer lock"):
            WriterLock.acquire(paths.root)
        assert (paths.root / ".writer.lock" / "metadata.json").exists()
    finally:
        first.release()
    assert not (paths.root / ".writer.lock").exists()
