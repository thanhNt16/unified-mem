"""kg query — unified intent-aware graph query. Returns packed markdown context."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

from kg.config import Config
from kg.embed import FakeEmbedder
from kg.pack import diversify_by_source, adaptive_budget, pack
from kg.router import (
    INTENT_CAP,
    INTENT_HOPS,
    INTENT_WEIGHTS,
    QueryPolicy,
    classify,
)
from kg.search import graph_aware_hybrid_search
from kg.storage.sqlite import SQLiteAdapter
from kg.chunking import slugify


def _token_count(text: str) -> int:
    import tiktoken
    return len(tiktoken.get_encoding("cl100k_base").encode(text))


def _write_query_note(wiki_notes_dir: Path, query: str, policy, packed_md: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = slugify(query)[:40] or "query"
    wiki_notes_dir.mkdir(parents=True, exist_ok=True)
    path = wiki_notes_dir / f"{ts}-{slug}.md"
    frontmatter = (
        f"---\n"
        f"query: {json.dumps(query)}\n"
        f"intent: {policy.mode}\n"
        f"budget: {policy.budget_tokens}\n"
        f"hops: {policy.hops}\n"
        f"ts: {ts}\n"
        f"---\n\n"
    )
    path.write_text(frontmatter + packed_md, encoding="utf-8")
    log = wiki_notes_dir.parent / "log.md"
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{ts} query={json.dumps(query)} intent={policy.mode}\n")
    return path


def _find_project_root(start: Path | None = None) -> Path:
    from kg.paths import KgPaths
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".kg" / "config.toml").is_file():
            return candidate
    try:
        return KgPaths.for_cwd().root.parent
    except Exception:
        raise FileNotFoundError("No .kg/config.toml found in cwd or any parent directory.")


def query_cli(
    query: Annotated[str, typer.Argument(help="Natural-language query")],
    budget_tokens: Annotated[int | None, typer.Option("--budget-tokens", help="Override token budget")] = None,
    mode: Annotated[str | None, typer.Option("--mode", help="find|trace|explain (override intent)")] = None,
    no_note: Annotated[bool, typer.Option("--no-note", help="Skip writing wiki note")] = False,
) -> None:
    """Run an intent-aware graph query. Prints packed markdown to stdout."""
    project_root = _find_project_root()
    config = Config.from_path(project_root / ".kg" / "config.toml")
    adapter = SQLiteAdapter(project_root / ".kg" / "kg.db")
    embedder = FakeEmbedder()

    policy = classify(query, config=config)
    if mode:
        m = mode if mode in INTENT_HOPS else "find"
        policy = QueryPolicy(
            mode=m,
            hops=INTENT_HOPS[m],
            cap=INTENT_CAP[m],
            budget_tokens=policy.budget_tokens,
            weights=INTENT_WEIGHTS[m],
        )

    ranked, subgraph = graph_aware_hybrid_search(
        adapter, embedder, query, policy, config=config, return_subgraph=True,
    )
    ranked = diversify_by_source(ranked, adapter, max_per_source=config.query.diversity_cap)
    effective_budget = budget_tokens or adaptive_budget(config.query.pack_budget_tokens, adapter)

    rrf_scores = {nid: score for nid, score in ranked}
    packed = (
        pack(subgraph, rrf_scores, rrf_scores=rrf_scores, budget_tokens=effective_budget)
        if subgraph else ""
    )

    typer.echo(packed)
    tokens = _token_count(packed)

    if not no_note:
        notes_dir = project_root / ".kg" / "wiki" / "notes"
        note_path = _write_query_note(notes_dir, query, policy, packed)
        typer.echo(f"\n[note: {note_path.name} | tokens: {tokens}]", err=True)

    log_dir = project_root / ".kg" / "bench-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "mode": policy.mode,
        "hits": len(ranked),
        "tokens": tokens,
    }
    with open(log_dir / "query-log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    adapter.conn.close()
