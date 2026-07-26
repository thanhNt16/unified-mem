"""Collision-safe, injection-safe entity page generation from active graph nodes."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from kg.chunking import slugify
from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter

_MANIFEST = ".kg-generated.json"
_MARKDOWN = re.compile(r"([\\`*_{}\[\]<>#+\-.!|])")


@dataclass
class SyncReport:
    pages_written: int = 0
    stale_removed: int = 0
    user_files_preserved: int = 0
    errors: list[str] = field(default_factory=list)


def _stable_suffix(node_id: str) -> str:
    return hashlib.sha256(node_id.encode()).hexdigest()[:8]


def _entity_filename(node: Node) -> str:
    slug = slugify(node.canonical_name or node.name) or "entity"
    return f"{slug}--{_stable_suffix(node.id or '')}.md"


def _text(value: Any) -> str:
    """Render untrusted text as one inert Markdown line."""
    value = " ".join(str(value or "").split())
    return _MARKDOWN.sub(r"\\\1", html.escape(value, quote=False)) or "—"


def _safe_raw_path(value: Any) -> str | None:
    """Accept only relative raw/... paths without traversal or URL syntax."""
    if not isinstance(value, str) or not value or urlsplit(value).scheme:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "raw":
        return None
    return value.replace("\\", "/")


def _source(source: Any) -> str:
    if not isinstance(source, dict):
        return _text(source)
    doc = source.get("doc", "?")
    chunk = source.get("chunk")
    suffix = f"#chunk-{_text(chunk)}" if chunk is not None else ""
    safe = _safe_raw_path(doc)
    if safe:
        href = "../../" + quote(safe, safe="/")
        return f"[{html.escape(safe, quote=False)}]({href}){suffix}"
    return _text(doc) + suffix


def _edge_line(edge: Edge, other: Node | None, filenames: dict[str, str]) -> str:
    other_id = ""
    try:
        source, _semantic, target = (edge.id or "").split("|", 2)
        other_id = target if other and other.id == target else source
    except ValueError:
        pass
    label = _text(other.canonical_name or other.name) if other else _text(other_id or edge.id)
    if other and other.id in filenames:
        label = f"[{label}]({filenames[other.id]})"
    return f"- {_text(edge.semantic_type)}: {label} (`{_text(other_id or edge.id)}`)"


def _render(node: Node, outgoing: list[tuple[Edge, Node | None]], incoming: list[tuple[Edge, Node | None]], filenames: dict[str, str]) -> str:
    aliases = ", ".join(_text(alias) for alias in sorted(node.aliases)) or "—"
    attrs = json.dumps(node.attributes, indent=2, sort_keys=True, ensure_ascii=False)
    attr_block = "\n".join(f"    {line}" for line in attrs.splitlines())
    sources = "\n".join(f"- {_source(source)}" for source in node.sources) or "—"
    out = "\n".join(_edge_line(edge, other, filenames) for edge, other in outgoing) or "—"
    inc = "\n".join(_edge_line(edge, other, filenames) for edge, other in incoming) or "—"
    subtype = f" / {_text(node.subtype)}" if node.subtype else ""
    return (
        f"# {_text(node.canonical_name or node.name)}\n\n"
        f"- **type:** {_text(node.type)}{subtype}\n"
        f"- **id:** `{_text(node.id)}`\n"
        f"- **status:** {_text(node.status)}\n"
        f"- **aliases:** {aliases}\n"
        f"- **valid_from:** {_text(node.valid_from)}\n"
        f"- **valid_until:** {_text(node.valid_until)}\n\n"
        f"## Summary\n\n{_text(node.summary)}\n\n"
        f"## Attributes\n\n{attr_block}\n\n"
        f"## Sources\n\n{sources}\n\n"
        f"## Outgoing\n\n{out}\n\n"
        f"## Incoming\n\n{inc}\n"
    )


def _atomic_write(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _load_manifest(path: Path) -> set[str]:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return {value for value in values if isinstance(value, str) and Path(value).name == value}


def sync_wiki(adapter: SQLiteAdapter, wiki_dir: Path) -> SyncReport:
    """Sync active nodes to ``wiki_dir/entities``; never delete user files."""
    entities = Path(wiki_dir) / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    manifest = entities / _MANIFEST
    old_files = _load_manifest(manifest)
    nodes = [Node.model_validate_json(row["data"]) for row in adapter.conn.execute(
        "SELECT data FROM nodes WHERE status='active' ORDER BY id"
    )]
    filenames = {node.id or "": _entity_filename(node) for node in nodes}
    active = {node.id or "": node for node in nodes}
    edges = [Edge.model_validate_json(row["data"]) for row in adapter.conn.execute(
        "SELECT data FROM edges WHERE status='active' ORDER BY semantic_type, id"
    )]
    outgoing: dict[str, list[tuple[Edge, Node | None]]] = {node_id: [] for node_id in active}
    incoming: dict[str, list[tuple[Edge, Node | None]]] = {node_id: [] for node_id in active}
    for edge in edges:
        try:
            source, _semantic, target = (edge.id or "").split("|", 2)
        except ValueError:
            continue
        if source in active:
            outgoing[source].append((edge, active.get(target)))
        if target in active:
            incoming[target].append((edge, active.get(source)))

    # Render everything before changing disk: rendering failures preserve prior state.
    pages = {filenames[node.id or ""]: _render(node, outgoing[node.id or ""], incoming[node.id or ""], filenames)
             for node in nodes}
    for filename in sorted(pages):
        _atomic_write(entities / filename, pages[filename])

    generated = set(pages)
    removed = 0
    for filename in old_files - generated:
        path = entities / filename
        if path.is_file():
            path.unlink()
            removed += 1
    _atomic_write(manifest, json.dumps(sorted(generated), indent=2) + "\n")
    preserved = sum(1 for path in entities.glob("*.md") if path.name not in generated)
    return SyncReport(pages_written=len(pages), stale_removed=removed, user_files_preserved=preserved)
