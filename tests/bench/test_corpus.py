"""Tests for bench.corpus — determinism, scale isolation, gray-zone pairs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench.corpus import make_corpus, make_corpus_at_scale


SCALES = [10, 50, 250]


def _corpus_files(corpus_dir: Path) -> list[Path]:
    return sorted(corpus_dir.glob("*.md"))


def _total_files(out_dir: Path) -> list[Path]:
    corpus_dir = out_dir / "corpus"
    return _corpus_files(corpus_dir)


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_make_corpus_creates_dir_structure(tmp_path: Path) -> None:
    manifest_path = make_corpus(5, tmp_path)
    assert manifest_path.exists()
    assert manifest_path.name == "manifest.json"
    assert (tmp_path / "corpus").is_dir()
    assert len(_corpus_files(tmp_path / "corpus")) == 5 + 3  # generated + gray + 2 dup


def test_determinism_same_seed_byte_identical(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    make_corpus(10, dir_a, seed=42)
    make_corpus(10, dir_b, seed=42)

    files_a = _total_files(dir_a)
    files_b = _total_files(dir_b)
    assert [f.name for f in files_a] == [f.name for f in files_b]
    for a, b in zip(files_a, files_b):
        assert _file_sha(a) == _file_sha(b), f"{a.name} differs across runs"


def test_different_seed_different_content(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    make_corpus(10, dir_a, seed=42)
    make_corpus(10, dir_b, seed=7)
    # Filenames depend on entities; content will differ in at least one place
    files_a = _total_files(dir_a)
    files_b = _total_files(dir_b)
    shas_a = {_file_sha(f) for f in files_a}
    shas_b = {_file_sha(f) for f in files_b}
    assert shas_a != shas_b, "different seeds must produce different content"


@pytest.mark.parametrize("scale", SCALES)
def test_scale_produces_exact_counts(scale: int, tmp_path: Path) -> None:
    out_dir = tmp_path / f"scale-{scale}"
    manifest_path = make_corpus(scale, out_dir, seed=42)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["doc_count"] == scale + 3
    assert manifest["generated_docs"] == scale
    assert len(_total_files(out_dir)) == scale + 3


def test_cross_document_entities_present(tmp_path: Path) -> None:
    make_corpus(10, tmp_path, seed=42)
    corpus_dir = tmp_path / "corpus"
    all_text = "".join(p.read_text() for p in _corpus_files(corpus_dir))
    # Cross-doc reference: at least one name appears in multiple docs
    name_counts = []
    from bench.corpus import _PERSONS

    for name, _ in _PERSONS[:10]:
        name_counts.append(all_text.count(name))
    assert max(name_counts) >= 2, "expected at least one person referenced across docs"


def test_gray_zone_pairs_present(tmp_path: Path) -> None:
    make_corpus(10, tmp_path, seed=42)
    gray_path = tmp_path / "corpus" / "gray-zone-paris.md"
    assert gray_path.exists()
    content = gray_path.read_text()
    # "Paris" must appear as both person and location references
    assert "Paris is a researcher" in content
    assert "Paris, the capital of France" in content


def test_near_duplicate_pair_present(tmp_path: Path) -> None:
    make_corpus(10, tmp_path, seed=42)
    dup_a = tmp_path / "corpus" / "near-dup-elena-vasquez-a.md"
    dup_b = tmp_path / "corpus" / "near-dup-elena-vasquez-b.md"
    assert dup_a.exists() and dup_b.exists()
    # Same entity, slightly different content
    text_a = dup_a.read_text()
    text_b = dup_b.read_text()
    assert "Elena Vasquez" in text_a and "Elena Vasquez" in text_b
    # Near-dup: content differs but is similar
    assert text_a != text_b
    # Share most lines
    lines_a = set(text_a.splitlines())
    lines_b = set(text_b.splitlines())
    common = lines_a & lines_b
    assert len(common) > len(lines_a) * 0.5


def test_no_secrets_in_corpus(tmp_path: Path) -> None:
    make_corpus(50, tmp_path, seed=42)
    corpus_dir = tmp_path / "corpus"
    secret_patterns = [
        "password",
        "secret",
        "api_key",
        "API_KEY",
        "token=",
        "BEGIN PRIVATE KEY",
        "sk-",
        "AKIA",  # AWS access key prefix
    ]
    for path in _corpus_files(corpus_dir):
        text = path.read_text()
        for pattern in secret_patterns:
            assert pattern not in text, f"secret pattern {pattern!r} in {path.name}"


def test_fresh_dir_isolation(tmp_path: Path) -> None:
    """Two scales must produce isolated dirs with no cross-contamination."""
    base = tmp_path / "bench"
    m10 = make_corpus_at_scale(10, base, seed=42)
    m50 = make_corpus_at_scale(50, base, seed=42)

    dir10 = base / "scale-10" / "corpus"
    dir50 = base / "scale-50" / "corpus"
    assert dir10.exists() and dir50.exists()
    assert dir10 != dir50

    files10 = {p.name for p in _corpus_files(dir10)}
    files50 = {p.name for p in _corpus_files(dir50)}
    # Gray-zone and near-dup files appear in both, but generated files differ by scale
    assert len(files10) == 13
    assert len(files50) == 53

    # Manifests record correct counts
    m10_data = json.loads(m10.read_text())
    m50_data = json.loads(m50.read_text())
    assert m10_data["generated_docs"] == 10
    assert m50_data["generated_docs"] == 50


def test_invalid_scale_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_corpus_at_scale(7, tmp_path, seed=42)


def test_manifest_records_metadata(tmp_path: Path) -> None:
    manifest_path = make_corpus(10, tmp_path, seed=42)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["seed"] == 42
    assert manifest["generated_docs"] == 10
    assert "generated_at" in manifest
    assert "checksums" in manifest
    assert len(manifest["checksums"]) == 10
    assert manifest["gray_zone_pairs"]
    assert manifest["near_duplicate_pairs"]


def test_filenames_deterministic(tmp_path: Path) -> None:
    """Filename sequence must be identical for same seed."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    make_corpus(10, dir_a, seed=42)
    make_corpus(10, dir_b, seed=42)
    names_a = [p.name for p in _corpus_files(dir_a / "corpus")]
    names_b = [p.name for p in _corpus_files(dir_b / "corpus")]
    assert names_a == names_b


def test_facts_and_preferences_present(tmp_path: Path) -> None:
    """Generated docs should include fact and preference entities."""
    make_corpus(20, tmp_path, seed=42)
    all_text = "".join(p.read_text() for p in _corpus_files(tmp_path / "corpus"))
    # Templates include "## Key Fact" / "## Technical Detail" / "## Fact"
    assert "## Fact" in all_text or "## Key Fact" in all_text or "## Technical Detail" in all_text
    assert "## Preferences" in all_text or "## Team Preferences" in all_text or "## Engineering Standards" in all_text


def test_no_randomness_at_module_import() -> None:
    """Module import must not call random at import time."""
    import importlib

    import bench.corpus as mod

    # Re-import should not raise and should not depend on global random state
    importlib.reload(mod)
    assert hasattr(mod, "make_corpus")
