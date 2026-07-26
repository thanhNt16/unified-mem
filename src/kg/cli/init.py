from __future__ import annotations
from pathlib import Path

import typer

from kg.paths import KgPaths
from kg.config import Config
from kg.ontology import write_ontology


def init_project(cwd: Path, user_id: str, scope: str) -> KgPaths:
    paths = KgPaths.for_root(cwd / ".kg")
    paths.ensure()

    if not paths.config.exists():
        cfg = Config.default(user_id=user_id, scope=scope)
        paths.config.write_text(cfg.render_toml(), encoding="utf-8")

    if not paths.ontology.exists():
        write_ontology(paths.ontology)

    paths.registry.touch(exist_ok=True)

    if not paths.wiki_index.exists():
        paths.wiki_index.write_text(
            f"# {scope} — Memory Index\n\n_Sources ingested into kg._\n\n",
            encoding="utf-8",
        )
    return paths


def init_cli(
    user_id: str = typer.Option("user", "--user-id", help="Owner id embedded in node IDs."),
    scope: str = typer.Option("default", "--scope", help="Per-project scope label."),
    from_snapshot: Path | None = typer.Option(None, "--from-snapshot", help="External snapshot artifact to restore."),
    force: bool = typer.Option(False, "--force", help="Replace an existing kg.db when restoring."),
) -> None:
    paths = init_project(Path.cwd(), user_id=user_id, scope=scope)
    if from_snapshot:
        from kg.snapshot import restore_snapshot
        restore_snapshot(from_snapshot, paths.kg_db, force=force)
        typer.echo(f"Restored kg.db from {from_snapshot}")
    typer.echo(f"Initialized kg memory at {paths.root}")
