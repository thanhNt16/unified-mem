from __future__ import annotations

import json
import sys

import typer

from kg.cypher import CypherError, parse, translate
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter


def cypher_cli(
    query: str = typer.Argument(..., help="Cypher read-only query"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Run a read-only Cypher query against the kg graph."""
    paths = KgPaths.for_cwd()
    adapter = SQLiteAdapter(paths.kg_db)
    ast = parse(query)
    rows = translate(ast, adapter)
    if json_output:
        typer.echo(json.dumps(rows))
        return
    for row in rows:
        if isinstance(row, dict) and "node_id" in row:
            typer.echo(
                f"{row.get('node_id','')}  {row.get('name','')}  "
                f"{row.get('type','')}  {row.get('summary','') or ''}"
            )
        else:
            typer.echo(row)
