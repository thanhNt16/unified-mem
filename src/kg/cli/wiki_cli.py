from __future__ import annotations

import typer

from kg.config import Config
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter
from kg.wiki import sync_wiki

wiki_app = typer.Typer()


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
