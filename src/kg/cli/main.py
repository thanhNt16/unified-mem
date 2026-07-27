from pathlib import Path

import typer
from kg import __version__

app = typer.Typer(
    name="kg",
    help="Portable, local-first unified memory layer.",
    no_args_is_help=True,
)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", help="Show kg version and exit.",
    ),
) -> None:
    if version:
        typer.echo(f"kg {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


from kg.cli import init as init_cmd  # noqa: E402
from kg.cli import install_cli  # noqa: E402
from kg.cli import raw as raw_cmd  # noqa: E402
from kg.cli import status as status_cmd  # noqa: E402
from kg.cli import config_cmd  # noqa: E402
from kg.cli import wiki_cli  # noqa: E402
from kg.cli import cypher_cli  # noqa: E402

app.command(name="init")(init_cmd.init_cli)
app.command(name="install")(install_cli.install)
app.add_typer(raw_cmd.raw_app, name="raw")
app.command(name="status")(status_cmd.status_cli)
config_app = typer.Typer(help="Read kg config.")
app.add_typer(config_app, name="config")
config_app.command(name="get")(config_cmd.config_cli)
app.add_typer(wiki_cli.wiki_app, name="wiki")

from kg.cli import save as save_cmd        # noqa: E402
from kg.cli import query as query_cmd      # noqa: E402
from kg.cli import resolve_cli as rcli     # noqa: E402

app.command(name="save")(save_cmd.save_cli)
app.command(name="search")(query_cmd.search_cli)
app.command(name="expand")(query_cmd.expand_cli)
app.command(name="pack")(query_cmd.pack_cli)
app.command(name="resolve")(rcli.resolve_cli)
app.command(name="dedup-check")(rcli.dedup_check_cli)
app.command(name="cypher")(cypher_cli.cypher_cli)

from kg.cli import snapshot_cmd  # noqa: E402
from kg.cli import review as review_cmd  # noqa: E402
from kg.cli import dream as dream_cmd  # noqa: E402
from kg.cli import viz as viz_cmd  # noqa: E402
from kg.cli import bench_cli as bench_cmd  # noqa: E402

app.command(name="snapshot")(snapshot_cmd.snapshot_cli)
app.command(name="merge")(review_cmd.merge_cli)
app.add_typer(review_cmd.review_app, name="review")
app.add_typer(dream_cmd.dream_app, name="dream")
app.command(name="viz")(viz_cmd.viz_cli)
app.command(name="bench")(bench_cmd.bench_cli)

mcp_app = typer.Typer(help="Run the kg MCP server.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command(name="serve")
def _mcp_serve(
    project_root: Path = typer.Option(
        ..., "--project-root", help="Project root containing .kg/."
    ),
    allow_writes: bool = typer.Option(
        False, "--allow-writes", help="Enable write tools (default: read-only)."
    ),
) -> None:
    """Serve kg over MCP stdio. stdout emits JSON-RPC only."""
    from kg.mcp.server import run_stdio

    run_stdio(project_root, allow_writes=allow_writes)


hook_app = typer.Typer(help="Harness hook entrypoints.")
app.add_typer(hook_app, name="hook")


@hook_app.command(name="session-end")
def _hook_session_end(
    project_root: Path = typer.Option(
        ..., "--project-root", help="Project root containing .kg/."
    ),
    session_root: Path | None = typer.Option(
        None, "--session-root",
        help="Trusted directory under --project-root that contains transcript files "
             "referenced by payload.transcript_path. Must be contained inside project root.",
    ),
) -> None:
    """Claude SessionEnd conversation-ingest hook. stdout is always empty."""
    import sys as _sys

    from kg.hooks.session_end import run as _run

    code = _run(
        project_root, stdin=_sys.stdin.buffer, stderr=_sys.stderr,
        session_root=session_root,
    )
    if code:
        raise typer.Exit(code)


if __name__ == "__main__":
    app()
