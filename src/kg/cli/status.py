from __future__ import annotations
import typer

from kg.paths import KgPaths
from kg.registry import Registry


def status_report(paths: KgPaths) -> dict:
    entries = Registry(paths.registry).all()
    extracted = [e for e in entries if e.extracted]
    return {
        "sources": len(entries),
        "unextracted": len(entries) - len(extracted),
        "extracted": len(extracted),
        "last_ingested": max((e.ingested_at for e in entries), default=None),
    }


def status_cli() -> None:
    paths = KgPaths.for_cwd()
    rep = status_report(paths)
    typer.echo(f"sources:     {rep['sources']}")
    typer.echo(f"extracted:   {rep['extracted']}")
    typer.echo(f"unextracted: {rep['unextracted']}")
    typer.echo(f"last ingest: {rep['last_ingested'] or '-'}")
