from __future__ import annotations

import typer

from kg.config import Config
from kg.embed import make_embedder
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter
from kg.wiki import sync_wiki

wiki_app = typer.Typer()

build_app = typer.Typer()
wiki_app.add_typer(build_app, name="build")


@wiki_app.command(name="sync")
def wiki_sync_cli() -> None:
    """Write collision-safe entity pages for all active graph nodes."""
    paths = KgPaths.for_cwd()
    Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    report = sync_wiki(adapter, paths.wiki)
    typer.echo(
        f"synced {report.pages_written} pages, "
        f"removed {report.stale_removed} stale, "
        f"preserved {report.user_files_preserved} user files"
    )


@build_app.command(name="from-query")
def wiki_build_from_query(
    query: str = typer.Argument(..., help="Search query for deep wiki."),
    hops: int = typer.Option(3, "--hops", min=1, max=5, help="Expand hops (1-5)."),
) -> None:
    """Build a scoped deep-search wiki from a query."""
    from kg.deepsearch import build_deep_wiki
    from kg.embed import FakeEmbedder

    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    embedder = FakeEmbedder()
    try:
        embedder = make_embedder(cfg)
    except Exception:
        pass
    report = build_deep_wiki(adapter, embedder, query, hops=hops, wiki_dir=paths.wiki, config=cfg)
    typer.echo(
        f"{'cached' if report.cached else 'built'} deep wiki: {report.slug} "
        f"({report.pages} pages, {report.node_count} nodes)"
    )


@wiki_app.command(name="lint")
def wiki_lint_cli() -> None:
    """Lint wiki/entity pages for orphans, broken links, stale summaries."""
    from kg.wiki_lint import lint

    paths = KgPaths.for_cwd()
    Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    issues = lint(paths.wiki / "entities", adapter)
    if not issues:
        typer.echo("no issues")
        return
    for issue in issues:
        typer.echo(f"{issue.kind}: {issue.path}: {issue.detail}")
    typer.echo(f"\n{len(issues)} issue(s)")
