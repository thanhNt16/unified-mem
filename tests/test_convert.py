import pytest
from kg.convert import convert_source, ConvertedDoc


def test_text_passthrough():
    doc = convert_source("hello world", type="text", title=None)
    assert isinstance(doc, ConvertedDoc)
    assert doc.markdown.strip() == "hello world"
    assert doc.title is None


def test_markdown_passthrough(tmp_path):
    f = tmp_path / "n.md"
    f.write_text("# Title\n\nbody")
    doc = convert_source(str(f), type=None, title=None)
    assert "# Title" in doc.markdown


def test_html_via_markitdown(tmp_path):
    f = tmp_path / "p.html"
    f.write_text("<html><body><h1>Hi</h1><p>There</p></body></html>")
    doc = convert_source(str(f), type=None, title=None)
    assert "There" in doc.markdown


def test_unknown_type_raises(tmp_path):
    with pytest.raises(ValueError):
        convert_source("x", type="bogus", title=None)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        convert_source(str(tmp_path / "nope.pdf"), type=None, title=None)


def test_auto_detect_url():
    # Detection only; network not exercised in unit test.
    from kg.convert import _detect_type
    assert _detect_type("https://example.com/a") == "url"
    assert _detect_type("/x/y.pdf") == "pdf"
    assert _detect_type("-") == "text"


def test_docx_empty_conversion_raises_clear_error(tmp_path, monkeypatch):
    docx = tmp_path / "body.docx"
    docx.touch()
    monkeypatch.setattr("kg.convert._convert_markitdown", lambda _: "")
    with pytest.raises(ValueError, match="DOCX conversion produced no content"):
        convert_source(str(docx), type=None, title=None)
