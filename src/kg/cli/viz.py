from __future__ import annotations

from pathlib import Path

import typer

from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter
from kg.viz.server import serve


def viz_cli(
    port: int = typer.Option(9749, "--port", help="Port on 127.0.0.1."),
    wiki: Path | None = typer.Option(
        None, "--wiki", help="Wiki directory (must contain entities/). Optional."
    ),
) -> None:
    """Serve the local graph UI on http://127.0.0.1:port/. Binds 127.0.0.1 only."""
    paths = KgPaths.for_cwd()
    adapter = SQLiteAdapter(paths.kg_db)
    serve(adapter, port=port, wiki_dir=wiki, open_browser=False)
