from __future__ import annotations

import json
from pathlib import Path

import typer

from kg.config import Config
from kg.dedup import Deduper
from kg.embed import make_embedder
from kg.gate import Gate
from kg.paths import KgPaths
from kg.resolve import Resolver
from kg.storage.sqlite import SQLiteAdapter


def _build_gate(paths: KgPaths, cfg: Config) -> Gate:
    adapter = SQLiteAdapter(paths.kg_db)
    emb = make_embedder(cfg)
    return Gate(
        adapter, Resolver(adapter, emb, cfg.thresholds, cfg.project.user_id),
        Deduper(adapter, emb, cfg.thresholds), emb, cfg, cfg.project.user_id,
    )


def save_cli(
    nodes: Path = typer.Option(..., "--nodes"),
    edges: Path = typer.Option(..., "--edges"),
    source: str = typer.Option(..., "--source"),
) -> None:
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    report = _build_gate(paths, cfg).normalize(
        json.loads(nodes.read_text()), json.loads(edges.read_text()), source,
    )
    for d in report.decisions:
        typer.echo(
            f"{d.action:9} {d.type:13} {d.name}  -> {d.target_id} "
            f"({d.score:.2f} via {d.via})"
        )
    typer.echo(f"edges: {report.edges_upserted}  new same_as: {report.new_same_as}")
