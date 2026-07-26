from __future__ import annotations

import typer

from kg.config import Config
from kg.dedup import Deduper
from kg.embed import make_embedder
from kg.ontology import Node
from kg.paths import KgPaths
from kg.resolve import Resolver
from kg.storage.sqlite import SQLiteAdapter


def _load():
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    return SQLiteAdapter(paths.kg_db), make_embedder(cfg), cfg


def resolve_cli(
    name: str = typer.Argument(...),
    type_: str = typer.Option(..., "--type"),
) -> None:
    ad, emb, cfg = _load()
    res = Resolver(ad, emb, cfg.thresholds, cfg.project.user_id).resolve(name, type_)
    typer.echo(f"{res.via}  {res.matched_id}  ({res.score:.2f})  canonical={res.canonical_name}")


def dedup_check_cli(node_json: str = typer.Argument(...)) -> None:
    ad, emb, cfg = _load()
    node = Node.model_validate_json(node_json)
    res = Deduper(ad, emb, cfg.thresholds).dedup(node, cfg.embedding.embed_fields)
    typer.echo(f"best={res.best_match_id}  score={res.score:.2f}")
