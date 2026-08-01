import pytest
from kg.registry import Registry, RegistryEntry


def _entry(sha="a", path="raw/x.md"):
    return RegistryEntry(sha256=sha, path=path, source="/src",
                         type="text", title="t", ingested_at="2026-07-26T00:00:00Z")


def test_append_and_has(tmp_path):
    reg = Registry(tmp_path / "registry.jsonl")
    assert reg.append(_entry()) is True
    assert reg.has("a") is True
    assert reg.append(_entry()) is False  # idempotent / dedupe


def test_get_and_all(tmp_path):
    reg = Registry(tmp_path / "registry.jsonl")
    reg.append(_entry("a", "raw/a.md"))
    reg.append(_entry("b", "raw/b.md"))
    assert reg.get("b").path == "raw/b.md"
    assert len(reg.all()) == 2


def test_unextracted_and_mark(tmp_path):
    reg = Registry(tmp_path / "registry.jsonl")
    reg.append(_entry("a"))
    reg.append(_entry("b"))
    assert len(reg.unextracted()) == 2
    reg.mark_extracted("a", chunks_done=[0, 1], chunks_failed={})
    assert len(reg.unextracted()) == 1
    assert reg.get("a").extracted is True
    assert reg.get("a").chunks_done == [0, 1]


def test_first_completed_chunk_keeps_source_partial(tmp_path):
    registry = Registry(tmp_path / "registry.jsonl")
    source = registry.add_raw("body", title="x", source_type="text", chunk_count=3)
    registry.mark_chunk(source.sha256, 0, "done")
    record = registry.get(source.sha256)
    assert record.status == "partial"
    assert record.chunks_done == [0]
