from __future__ import annotations
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class ConvertedDoc:
    markdown: str
    title: str | None


_TYPE_BY_SUFFIX = {
    "md": "md", "markdown": "md", "html": "html", "htm": "html",
    "pdf": "pdf", "docx": "docx",
}


def _detect_type(source: str) -> str:
    if source == "-":
        return "text"
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return "url"
    suffix = Path(source).suffix.lower().lstrip(".")
    return _TYPE_BY_SUFFIX.get(suffix, "text")


def _convert_markitdown(path: str) -> str:
    from markitdown import MarkItDown
    return MarkItDown().convert(path).text_content


def _convert_url(url: str) -> str:
    import trafilatura
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError(f"Could not fetch URL: {url}")
    return trafilatura.extract(downloaded) or ""


def convert_source(source: str, type: str | None, title: str | None) -> ConvertedDoc:
    kind = type or _detect_type(source)
    if kind == "text":
        md = source if source != "-" else sys.stdin.read()
    elif kind in ("md", "markdown"):
        md = Path(source).read_text(encoding="utf-8")
    elif kind == "url":
        md = _convert_url(source)
    elif kind in ("html", "pdf", "docx"):
        if not Path(source).exists():
            raise FileNotFoundError(source)
        md = _convert_markitdown(source)
    else:
        raise ValueError(f"Unsupported source type: {kind!r}")
    return ConvertedDoc(markdown=md, title=title)
