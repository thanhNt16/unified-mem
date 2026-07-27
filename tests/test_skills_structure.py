# tests/test_skills_structure.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "kg" / "skills"


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


def test_dream_skill_exists_and_bounded():
    md = (ROOT / "kg-dream" / "SKILL.md").read_text(encoding="utf-8")
    assert len(md.splitlines()) < 500
    assert "kg dream candidates" in md
    assert "kg merge" in md
    assert "kg review confirm" in md
    assert "kg review reject" in md
    # Skill must explicitly disclaim the implicit marker anti-pattern.
    assert "no `last_dream.txt`" in md or "no marker" in md.lower()
    assert (ROOT / "kg-dream" / "references" / "review-protocol.md").exists()
