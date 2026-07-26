import pytest
from kg.frontmatter import RawFrontmatter, render, parse


def test_render_parse_roundtrip():
    fm = RawFrontmatter(
        source="/abs/paper.pdf", sha256="abc123", type="pdf",
        title="My Paper", ingested_at="2026-07-26T12:00:00Z",
        chunks=[{"index": 0, "start_char": 0, "end_char": 10, "token_count": 3}],
    )
    doc = render(fm, "body text")
    assert doc.startswith("---\n")
    fm2, body = parse(doc)
    assert fm2.sha256 == "abc123"
    assert fm2.title == "My Paper"
    assert fm2.chunks[0]["token_count"] == 3
    assert body == "body text"


def test_parse_rejects_missing_frontmatter():
    with pytest.raises(ValueError):
        parse("no frontmatter here")
