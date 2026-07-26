import pytest
from kg import KgError
from kg.paths import KgPaths


def test_for_root_builds_all_paths(tmp_path):
    p = KgPaths.for_root(tmp_path / ".kg")
    assert p.raw == tmp_path / ".kg" / "raw"
    assert p.raw_conversations == tmp_path / ".kg" / "raw" / "conversations"
    assert p.wiki == tmp_path / ".kg" / "wiki"
    assert p.wiki_index == tmp_path / ".kg" / "wiki" / "index.md"
    assert p.registry == tmp_path / ".kg" / "registry.jsonl"
    assert p.ontology == tmp_path / ".kg" / "ontology.json"
    assert p.config == tmp_path / ".kg" / "config.toml"
    assert p.snapshots == tmp_path / ".kg" / "snapshots"
    assert p.review == tmp_path / ".kg" / "review"
    assert p.kg_db == tmp_path / ".kg" / "kg.db"


def test_ensure_creates_dirs(tmp_path):
    p = KgPaths.for_root(tmp_path / ".kg")
    p.ensure()
    assert p.raw.is_dir()
    assert p.raw_conversations.is_dir()
    assert p.wiki.is_dir()
    assert p.snapshots.is_dir()
    assert p.review.is_dir()


def test_for_cwd_raises_when_no_kg_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(KgError):
        KgPaths.for_cwd()
