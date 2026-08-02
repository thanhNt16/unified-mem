from threading import Event

import pytest

from kg.viz.indexing import IndexBusy, IndexManager, InvalidProjectPath


def test_rejects_missing_or_relative_project_path(tmp_path) -> None:
    manager = IndexManager(lambda root, name: None)
    with pytest.raises(InvalidProjectPath):
        manager.start("relative/path", "demo")
    with pytest.raises(InvalidProjectPath):
        manager.start(str(tmp_path / "missing"), "demo")
    with pytest.raises(InvalidProjectPath):
        manager.start(str(tmp_path) + "\x00bad", "demo")


def test_allows_only_one_index_job(tmp_path) -> None:
    entered, release = Event(), Event()

    def runner(root, name):
        entered.set()
        release.wait(timeout=2)

    manager = IndexManager(runner)
    first = manager.start(str(tmp_path), "demo")
    assert entered.wait(timeout=1)
    with pytest.raises(IndexBusy):
        manager.start(str(tmp_path), "second")
    release.set()
    first.thread.join(timeout=2)
    assert manager.status()[0]["status"] == "done"


def test_records_runner_error(tmp_path) -> None:
    def runner(root, name):
        raise RuntimeError("index failed")

    manager = IndexManager(runner)
    job = manager.start(str(tmp_path), "demo")
    job.thread.join(timeout=2)
    assert manager.status()[0]["status"] == "error"
    assert manager.status()[0]["error"] == "index failed"
