from __future__ import annotations
from pathlib import Path

import typer

from kg.config import Config


def _walk(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if not hasattr(cur, part):
            raise KeyError(dotted)
        cur = getattr(cur, part)
    return cur


def config_get(kg_root: Path, key: str):
    cfg = Config.from_path(kg_root / "config.toml")
    return _walk(cfg, key)


def config_cli(key: str = typer.Argument(...)) -> None:
    from kg.paths import KgPaths
    val = config_get(KgPaths.for_cwd().root, key)
    typer.echo(val)
