from kg.file_hashes import FileHashRegistry


def test_classify_reports_new_unchanged_changed_and_deleted(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    registry = FileHashRegistry()
    first = registry.classify("p", root)
    assert [item.rel_path for item in first.new] == ["a.py"]
    registry.apply(first, extractor_version="v1")
    registry.write_atomic(tmp_path / "file_hashes.jsonl")

    assert FileHashRegistry.load(
        tmp_path / "file_hashes.jsonl"
    ).classify("p", root).unchanged[0].rel_path == "a.py"
    (root / "a.py").write_text("x = 2\n")
    changed = registry.classify("p", root)
    assert [item.rel_path for item in changed.changed] == ["a.py"]
    (root / "a.py").unlink()
    assert [item.rel_path for item in registry.classify("p", root).deleted] == ["a.py"]
