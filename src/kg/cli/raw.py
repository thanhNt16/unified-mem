from __future__ import annotations
import typer

from kg.paths import KgPaths
from kg.config import Config
from kg.raw_ops import add_source, list_sources

raw_app = typer.Typer(help="Manage raw sources under .kg/raw/.")


@raw_app.command("add")
def raw_add(
    source: str = typer.Argument(..., help="Path, URL, or '-' for stdin."),
    type: str = typer.Option(None, "--type", help="pdf|docx|md|html|url|text"),
    title: str = typer.Option(None, "--title"),
    conversation: bool = typer.Option(False, "--conversation"),
) -> None:
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    added, rel = add_source(paths, cfg, source, type, title, conversation)
    if added:
        typer.echo(f"added: {rel}")
        typer.echo("next: run /kg-extract")
    else:
        typer.echo(f"skipped (duplicate): {rel}")


@raw_app.command("list")
def raw_list(
    unextracted: bool = typer.Option(False, "--unextracted"),
) -> None:
    paths = KgPaths.for_cwd()
    for e in list_sources(paths, unextracted_only=unextracted):
        flag = "" if e.extracted else " [unextracted]"
        typer.echo(f"{e.path}\t{e.type}\t{e.title}{flag}")
