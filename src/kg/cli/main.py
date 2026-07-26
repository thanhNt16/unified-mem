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

app.command(name="init")(init_cmd.init_cli)
