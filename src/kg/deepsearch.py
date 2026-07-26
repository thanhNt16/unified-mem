"""Deep-search wiki: hybrid_search -> expand -> materialize scoped wiki pages."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from kg.chunking import slugify
from kg.ontology import Edge, Node
from kg.search import hybrid_search
from kg.traverse import expand
from kg.wiki import _atomic_write, _edge_line, _entity_filename, _text


@dataclass
class DeepReport:
    path: Path
    slug: str
    pages: int
    cached: bool
    node_count: int
    max_updated_at: str | None


def _deep_slug(query: str) -> str:
    if not isinstance(query, str) or not query or ".." in query.replace("\\", "/").split("/"):
        raise ValueError("invalid query path")
    base = slugify(query, max_len=60) or "query"
    suffix = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    return f"{base}--{suffix}"


def _graph_version(adapter) -> tuple[int, str | None]:
    # updated_at lives in the canonical JSON, not a SQLite column.
    rows = adapter.conn.execute(
        "SELECT data FROM nodes WHERE status='active'"
    ).fetchall()
    nodes = [Node.model_validate_json(row["data"]) for row in rows]
    return len(nodes), max((n.updated_at or "" for n in nodes), default="") or None


def _load_cache_meta(deep_dir: Path) -> dict | None:
    meta = deep_dir / ".deep-meta.json"
    if not meta.is_file():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache_meta(deep_dir: Path, node_count: int, max_updated_at: str | None, pages: int) -> None:
    meta = deep_dir / ".deep-meta.json"
    _atomic_write(meta, json.dumps({
        "node_count": node_count,
        "max_updated_at": max_updated_at,
        "pages": pages,
    }, indent=2) + "\n")


def _render_deep_page(
    node: Node,
    outgoing: list[tuple[Edge, Node | None]],
    incoming: list[tuple[Edge, Node | None]],
    filenames: dict[str, str],
) -> str:
    aliases = ", ".join(_text(a) for a in sorted(node.aliases)) or "—"
    attrs = json.dumps(node.attributes, indent=2, sort_keys=True, ensure_ascii=False)
    attr_block = "\n".join(f"    {line}" for line in attrs.splitlines())
    out = "\n".join(_edge_line(e, o, filenames) for e, o in outgoing) or "—"
    inc = "\n".join(_edge_line(e, o, filenames) for e, o in incoming) or "—"
    subtype = f" / {_text(node.subtype)}" if node.subtype else ""
    return (
        f"# {_text(node.canonical_name or node.name)}\n\n"
        f"- **type:** {_text(node.type)}{subtype}\n"
        f"- **id:** `{_text(node.id)}`\n"
        f"- **aliases:** {aliases}\n\n"
        f"## Summary\n\n{_text(node.summary)}\n\n"
        f"## Attributes\n\n{attr_block}\n\n"
        f"## Outgoing\n\n{out}\n\n"
        f"## Incoming\n\n{inc}\n"
    )


def _render_index(query: str, nodes: list[Node], filenames: dict[str, str]) -> str:
    lines = [f"# Deep search: {_text(query)}\n"]
    for node in nodes:
        fname = filenames.get(node.id or "", "")
        if fname:
            label = _text(node.canonical_name or node.name)
            lines.append(f"- [[{fname}|{label}]]")
    return "\n".join(lines) + "\n"


def build_deep_wiki(
    adapter,
    embedder,
    query: str,
    *,
    hops: int = 3,
    wiki_dir: Path,
    config=None,
) -> DeepReport:
    """hybrid_search -> expand -> materialize ``wiki_dir/deep/<slug>/``."""
    slug = _deep_slug(query)
    deep_dir = Path(wiki_dir) / "deep" / slug

    node_count, max_updated_at = _graph_version(adapter)
    cached = _load_cache_meta(deep_dir)
    if (
        cached
        and cached.get("node_count") == node_count
        and cached.get("max_updated_at") == max_updated_at
    ):
        return DeepReport(
            path=deep_dir, slug=slug,
            pages=cached.get("pages", 0), cached=True,
            node_count=node_count, max_updated_at=max_updated_at,
        )

    ranked = hybrid_search(adapter, embedder, query, k=10, config=config)
    if not ranked:
        deep_dir.mkdir(parents=True, exist_ok=True)
        _write_cache_meta(deep_dir, node_count, max_updated_at, 0)
        return DeepReport(
            path=deep_dir, slug=slug, pages=0, cached=False,
            node_count=node_count, max_updated_at=max_updated_at,
        )

    seed_ids = [nid for nid, _ in ranked]
    sg = expand(adapter, seed_ids, hops=hops, cap=300)

    node_map: dict[str, Node] = {n.id: n for n in sg.nodes}
    filenames: dict[str, str] = {nid: _entity_filename(n) for nid, n in node_map.items()}

    outgoing: dict[str, list[tuple[Edge, Node | None]]] = {nid: [] for nid in node_map}
    incoming: dict[str, list[tuple[Edge, Node | None]]] = {nid: [] for nid in node_map}
    for edge in sg.edges:
        try:
            src, _, tgt = (edge.id or "").split("|", 2)
        except ValueError:
            continue
        if src in node_map:
            outgoing[src].append((edge, node_map.get(tgt)))
        if tgt in node_map:
            incoming[tgt].append((edge, node_map.get(src)))

    deep_dir.mkdir(parents=True, exist_ok=True)

    for node in sorted(node_map.values(), key=lambda n: n.id or ""):
        nid = node.id or ""
        page = _render_deep_page(node, outgoing[nid], incoming[nid], filenames)
        _atomic_write(deep_dir / filenames[nid], page)

    index = _render_index(query, sg.nodes, filenames)
    _atomic_write(deep_dir / "index.md", index)

    page_count = len(sg.nodes) + 1
    _write_cache_meta(deep_dir, node_count, max_updated_at, page_count)

    return DeepReport(
        path=deep_dir, slug=slug, pages=page_count, cached=False,
        node_count=node_count, max_updated_at=max_updated_at,
    )
