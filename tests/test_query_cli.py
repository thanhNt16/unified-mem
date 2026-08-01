"""Unit tests for kg query CLI note-writing helpers."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

import pytest

from kg.cli.query_cli import _write_query_note


@dataclass(frozen=True)
class _MockPolicy:
    mode: str = "find"
    hops: int = 1
    budget_tokens: int = 4000


def test_write_query_note_creates_file_and_log(tmp_path: Path) -> None:
    notes_dir = tmp_path / "wiki" / "notes"
    policy = _MockPolicy(mode="find", hops=1, budget_tokens=4000)
    packed = "# kg context\n\n## Foo (object)\n0.500 - sources: x\nFoo summary\n\n"

    note_path = _write_query_note(notes_dir, "what is Foo?", policy, packed)

    assert note_path.exists()
    assert note_path.parent == notes_dir
    assert note_path.suffix == ".md"
    assert note_path.name.endswith("-what-is-foo.md") or "foo" in note_path.name

    content = note_path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "query: \"what is Foo?\"" in content
    assert "intent: find" in content
    assert "budget: 4000" in content
    assert "hops: 1" in content
    assert "ts: " in content
    assert "Foo summary" in content

    log = notes_dir.parent / "log.md"
    assert log.exists()
    log_line = log.read_text(encoding="utf-8").strip()
    assert "query=\"what is Foo?\"" in log_line
    assert "intent=find" in log_line


def test_write_query_note_appends_multiple(tmp_path: Path) -> None:
    notes_dir = tmp_path / "wiki" / "notes"
    policy = _MockPolicy(mode="trace", hops=3, budget_tokens=2000)

    _write_query_note(notes_dir, "first question", policy, "p1")
    _write_query_note(notes_dir, "second question", policy, "p2")

    log = (notes_dir.parent / "log.md").read_text(encoding="utf-8")
    lines = [line for line in log.strip().splitlines() if line]
    assert len(lines) == 2
    assert "query=\"first question\"" in lines[0]
    assert "query=\"second question\"" in lines[1]


def test_write_query_note_frontmatter_keys(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    policy = _MockPolicy(mode="explain", hops=2, budget_tokens=8000)

    note_path = _write_query_note(notes_dir, "explain architecture", policy, "body")
    content = note_path.read_text(encoding="utf-8")

    # frontmatter must close before markdown body
    parts = content.split("---\n")
    assert len(parts) >= 3
    fm = parts[1]
    for key in ("query:", "intent:", "budget:", "hops:", "ts:"):
        assert key in fm
    assert "intent: explain" in fm
    assert "hops: 2" in fm
    assert "budget: 8000" in fm
