from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from kg.config import Config
from kg.paths import KgPaths
from kg.convert import convert_source, detect_type
from kg.chunking import chunk_markdown, slugify
from kg.frontmatter import RawFrontmatter, render
from kg.registry import Registry, RegistryEntry


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _raw_relpath(type_: str, title: str | None, sha: str,
                 conversation: bool) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = title or sha[:12]
    name = f"{today}--{type_}--{slugify(base)}.md"
    if conversation:
        return f"raw/conversations/{name}"
    return f"raw/{name}"


def add_source(paths: KgPaths, config: Config, source: str,
               type: str | None, title: str | None,
               conversation: bool = False) -> tuple[bool, str]:
    effective_type = type or detect_type(source)
    doc = convert_source(source, type=effective_type, title=title)
    sha = _sha256(doc.markdown)
    reg = Registry(paths.registry)
    if reg.has(sha):
        existing = reg.get(sha)
        return False, existing.path

    chunks = chunk_markdown(
        doc.markdown,
        tokens=config.chunking.tokens,
        overlap=config.chunking.overlap,
    )
    chunk_dicts = [
        {"index": c.index, "start_char": c.start_char,
         "end_char": c.end_char, "token_count": c.token_count}
        for c in chunks
    ]
    resolved_title = doc.title or ""
    fm = RawFrontmatter(
        source=source,
        sha256=sha,
        type=effective_type,
        title=resolved_title,
        ingested_at=datetime.now(timezone.utc).isoformat(),
        chunks=chunk_dicts,
    )
    rel = _raw_relpath(fm.type, resolved_title or None, sha, conversation)
    out = paths.root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(fm, doc.markdown), encoding="utf-8")

    reg.append(RegistryEntry(
        sha256=sha, path=rel, source=source, type=fm.type,
        title=resolved_title, ingested_at=fm.ingested_at,
    ))
    return True, rel


def list_sources(paths: KgPaths, unextracted_only: bool) -> list[RegistryEntry]:
    reg = Registry(paths.registry)
    return reg.unextracted() if unextracted_only else reg.all()
