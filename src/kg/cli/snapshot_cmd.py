from __future__ import annotations

from pathlib import Path

import typer

from kg.paths import KgPaths
from kg.snapshot import create_snapshot


def snapshot_cli(
    output: Path = typer.Option(None, "--output", "-o", help="Output path for snapshot artifact."),
) -> None:
    paths = KgPaths.for_cwd()
    dest = output if output else paths.snapshots / "kg.db.zst"
    result = create_snapshot(paths.kg_db, dest)
    typer.echo(str(result))
