from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from kg.cascade import prioritize_chunks
from kg.chunking import chunk_markdown
from kg.config import Config
from kg.dedup import Deduper
from kg.embed import make_embedder
from kg.gate import Gate
from kg.paths import KgPaths
from kg.registry import Registry
from kg.resolve import Resolver
from kg.storage.sqlite import SQLiteAdapter


def _build_gate(paths: KgPaths, cfg: Config) -> Gate:
    adapter = SQLiteAdapter(paths.kg_db)
    emb = make_embedder(cfg)
    return Gate(
        adapter, Resolver(adapter, emb, cfg.thresholds),
        Deduper(adapter, emb, cfg.thresholds), emb, cfg, cfg.project.user_id,
    )


def save_cli(
    nodes: Path = typer.Option(..., "--nodes"),
    edges: Path = typer.Option(..., "--edges"),
    source: str = typer.Option(..., "--source"),
    chunks: Optional[Path] = typer.Option(
        None, "--chunks", help="Markdown source to prioritize with --cascade.",
    ),
    facts: Optional[Path] = typer.Option(None, "--facts"),
    preferences: Optional[Path] = typer.Option(None, "--preferences"),
    cascade: bool = typer.Option(
        False, "--cascade", help="Advisory: rank chunks by entity density before save.",
    ),
    cascade_budget: int = typer.Option(
        10, "--cascade-budget", help="Number of high-priority chunks (advisory).",
    ),
) -> None:
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)

    if cascade and chunks:
        markdown = chunks.read_text(encoding="utf-8")
        texts = [chunk.text for chunk in chunk_markdown(
            markdown, tokens=cfg.chunking.tokens, overlap=cfg.chunking.overlap,
        )]
        ranked = prioritize_chunks(texts, budget=cascade_budget)
        # Advisory only — all chunks stay queued after the high-priority budget.
        typer.echo(
            f"cascade: {len(ranked)} chunks, budget={cascade_budget} (advisory)",
            err=True,
        )
    elif cascade:
        typer.echo("cascade: no --chunks input; save remains unfiltered", err=True)

    report = _build_gate(paths, cfg).normalize(
        json.loads(nodes.read_text()),
        json.loads(edges.read_text()),
        source,
        facts=json.loads(facts.read_text()) if facts else None,
        preferences=json.loads(preferences.read_text()) if preferences else None,
    )
    for d in report.decisions:
        typer.echo(
            f"{d.action:9} {d.type:13} {d.name}  -> {d.target_id} "
            f"({d.score:.2f} via {d.via})"
        )
    typer.echo(f"edges: {report.edges_upserted}  new same_as: {report.new_same_as}")
    typer.echo(f"dropped edges: {len(report.dropped_edges)}")

    # Mark source as extracted in registry (Bug 1)
    source_path = source.split("#")[0]
    registry = Registry(paths.registry)
    for entry in registry.all():
        if entry.path == source_path:
            registry.mark_extracted(entry.sha256, chunks_done=[], chunks_failed={})
            break
