"""Lint ``wiki/entities/`` for orphans, broken wikilinks, stale summaries."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kg.ontology import Node
from kg.wiki import _MANIFEST

_MAX_FILES = 10_000

# Match [[target]] or [[target|label]]; capture the target.
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# Markdown reference-style [label](target) where target is a local .md file.
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+\.md)(?:\s+\"[^\"]*\")?\)")


@dataclass(frozen=True)
class Issue:
    kind: str          # ORPHAN | BROKEN_LINK | STALE_SUMMARY
    path: str          # relative path to the offending page
    detail: str        # human-readable, deterministic


def _filename_to_node_id(adapter) -> dict[str, str]:
    """Map ``<slug>--<hash>.md`` filename -> node_id, for active nodes.

    The hash suffix is sha256(node_id)[:8], but we resolve via the manifest's
    pattern: load each active node and recompute its filename.
    """
    from kg.wiki import _entity_filename
    out: dict[str, str] = {}
    rows = adapter.conn.execute(
        "SELECT data FROM nodes WHERE status='active'"
    ).fetchall()
    for row in rows:
        try:
            node = Node.model_validate_json(row["data"])
        except Exception:
            continue
        if not node.id:
            continue
        out[_entity_filename(node)] = node.id
    return out


def _node_summaries(adapter) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = adapter.conn.execute(
        "SELECT data FROM nodes WHERE status='active'"
    ).fetchall()
    for row in rows:
        try:
            node = Node.model_validate_json(row["data"])
        except Exception:
            continue
        if node.id:
            out[node.id] = (node.summary or "").strip()
    return out


def _page_summary(text: str) -> str:
    """Extract the body of the '## Summary' section, or empty if missing."""
    idx = text.find("## Summary")
    if idx < 0:
        return ""
    rest = text[idx + len("## Summary"):]
    end = rest.find("\n## ")
    body = rest if end < 0 else rest[:end]
    return body.strip()


def _safe_join(base: Path, target: str) -> Path | None:
    """Reject traversal; return the contained target path, or None if unsafe."""
    if not target or any(ord(c) < 32 for c in target):
        return None
    if target.startswith(("http://", "https://", "mailto:", "#", "/")):
        return None
    pp = PurePosixPath(target.replace("\\", "/"))
    if pp.is_absolute() or ".." in pp.parts:
        return None
    return base / Path(*pp.parts)


def _iter_markdown_files(entities: Path) -> list[Path]:
    files = [p for p in entities.iterdir() if p.is_file() and p.suffix == ".md"]
    files.sort(key=lambda p: p.name)
    if len(files) > _MAX_FILES:
        return files[:_MAX_FILES]
    return files


def lint(entities_dir: Path, adapter) -> list[Issue]:
    """Lint ``wiki/entities/``; deterministic output sorted by (path, kind)."""
    entities = Path(entities_dir)
    if not entities.is_dir():
        return []

    issues: list[Issue] = []
    files = _iter_markdown_files(entities)

    manifest: set[str] = set()
    try:
        manifest = set(json.loads((entities / _MANIFEST).read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    fn_to_id = _filename_to_node_id(adapter)
    summaries = _node_summaries(adapter)

    for path in files:
        rel = path.name  # entities/<rel>; lint output stays relative to entities/
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        is_managed = rel in manifest or rel in fn_to_id
        if is_managed and rel not in fn_to_id:
            # Manifest-tracked file with no matching active node.
            issues.append(Issue("ORPHAN", rel, "page has no matching active node"))

        for pattern in (_WIKILINK, _MD_LINK):
            for match in pattern.finditer(text):
                target = match.group(1).strip()
                # Reject any traversal before splitting — a target with '..'
                # anywhere is unsafe regardless of where it lands after split.
                if ".." in target.replace("\\", "/").split("/"):
                    continue
                target_name = target.split("/")[-1].split("#")[0]
                if not target_name:
                    continue
                resolved = _safe_join(entities, target_name)
                if resolved is None:
                    continue
                if not resolved.exists():
                    issues.append(Issue(
                        "BROKEN_LINK", rel,
                        f"link target does not exist: {target_name}",
                    ))

        if rel in fn_to_id:
            node_id = fn_to_id[rel]
            node_summary = summaries.get(node_id, "")
            page_summary = _page_summary(text)
            if node_summary and page_summary and page_summary != node_summary:
                issues.append(Issue(
                    "STALE_SUMMARY", rel,
                    "page summary does not match node summary",
                ))

    issues.sort(key=lambda i: (i.path, i.kind, i.detail))
    return issues
