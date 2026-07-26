# tests/test_skills_structure.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"


def test_extract_skill_exists_and_bounded():
    md = (ROOT / "kg-extract" / "SKILL.md").read_text(encoding="utf-8")
    assert len(md.splitlines()) < 500
    assert "kg save" in md
    assert (ROOT / "kg-extract" / "references" / "extraction-prompt.md").exists()
    assert (ROOT / "kg-extract" / "references" / "output-schema.json").exists()


def test_query_skill_exists_and_bounded():
    md = (ROOT / "kg-query" / "SKILL.md").read_text(encoding="utf-8")
    assert len(md.splitlines()) < 500
    assert "kg search" in md
    assert (ROOT / "kg-query" / "references" / "query-modes.md").exists()
