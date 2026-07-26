from __future__ import annotations
import re
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

_H1 = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def first_heading(markdown: str) -> str | None:
    m = _H1.search(markdown)
    return m.group(1).strip() if m else None


def detect_type(source: str) -> str:
    if source == "-":
        return "text"
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return "url"
    suffix = Path(source).suffix.lower().lstrip(".")
    return _TYPE_BY_SUFFIX.get(suffix, "text")


def _detect_type(source: str) -> str:
    # back-compat alias for any external caller / tests
    return detect_type(source)


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
    if kind in ("text", "conversation"):
        # conversation transcripts are passed as the raw markdown body
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
    resolved_title = title if title is not None else first_heading(md)
    return ConvertedDoc(markdown=md, title=resolved_title)
