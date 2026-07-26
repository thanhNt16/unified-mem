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
from kg.cli import raw as raw_cmd  # noqa: E402
from kg.cli import status as status_cmd  # noqa: E402
from kg.cli import config_cmd  # noqa: E402
from kg.cli import wiki_cli  # noqa: E402

app.command(name="init")(init_cmd.init_cli)
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

from kg.cli import snapshot_cmd  # noqa: E402
from kg.cli import review as review_cmd  # noqa: E402
from kg.cli import dream as dream_cmd  # noqa: E402

app.command(name="snapshot")(snapshot_cmd.snapshot_cli)
app.command(name="merge")(review_cmd.merge_cli)
app.add_typer(review_cmd.review_app, name="review")
app.add_typer(dream_cmd.dream_app, name="dream")
