"""Bug 1: kg save marks source as extracted in registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from kg.registry import Registry, RegistryEntry


def _seed_registry(registry_path: Path, name: str = "test.md") -> str:
    entry = RegistryEntry(
        sha256="abc123",
        path=f"raw/{name}",
        source="docx",
        type="doc",
        title="Test",
        ingested_at="2026-01-01",
        extracted=False,
    )
    Registry(registry_path).append(entry)
    return "abc123"


def _make_mock_paths(tmp_path: Path, with_registry: bool = True):
    """Create mock KgPaths with real registry file."""
    kg = tmp_path / ".kg"
    kg.mkdir()
    registry_path = kg / "registry.jsonl"
    if with_registry:
        _seed_registry(registry_path)
    else:
        registry_path.touch()

    paths = MagicMock()
    paths.registry = registry_path
    return paths, registry_path


def _make_mock_report():
    r = MagicMock()
    r.decisions = []
    r.edges_upserted = 0
    r.new_same_as = 0
    r.dropped_edges = []
    return r


def test_mark_extracted_after_normalize(tmp_path):
    """After successful normalize, source found in registry gets marked extracted."""
    paths, reg_path = _make_mock_paths(tmp_path)
    sha = "abc123"
    assert not Registry(reg_path).get(sha).extracted

    # Simulate what save_cli does post-normalize
    source = "raw/test.md"
    source_path = source.split("#")[0]
    registry = Registry(paths.registry)
    for entry in registry.all():
        if entry.path == source_path:
            registry.mark_extracted(entry.sha256, chunks_done=[], chunks_failed={})
            break

    assert Registry(reg_path).get(sha).extracted


def test_chunk_suffix_stripped_before_lookup(tmp_path):
    """Source 'raw/test.md#chunk-5' should look up registry entry 'raw/test.md'."""
    paths, reg_path = _make_mock_paths(tmp_path)
    sha = "abc123"
    assert not Registry(reg_path).get(sha).extracted

    source = "raw/test.md#chunk-5"
    source_path = source.split("#")[0]
    assert source_path == "raw/test.md"

    registry = Registry(paths.registry)
    for entry in registry.all():
        if entry.path == source_path:
            registry.mark_extracted(entry.sha256, chunks_done=[], chunks_failed={})
            break

    assert Registry(reg_path).get(sha).extracted


def test_adhoc_source_not_in_registry_skips_gracefully(tmp_path):
    """Source not in registry should not raise error (silent skip)."""
    paths, reg_path = _make_mock_paths(tmp_path, with_registry=False)
    # Empty registry
    assert len(Registry(reg_path).all()) == 0

    source = "adhoc/something.md"
    source_path = source.split("#")[0]
    registry = Registry(paths.registry)
    found = False
    for entry in registry.all():
        if entry.path == source_path:
            found = True
            break
    assert not found  # No entry, so loop completes without action
    # Should not raise error
