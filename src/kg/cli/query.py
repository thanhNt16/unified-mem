from __future__ import annotations

import json
import sys

import typer

from kg.config import Config
from kg.embed import make_embedder
from kg.ontology import Edge, Node
from kg.pack import pack
from kg.paths import KgPaths
from kg.search import hybrid_search
from kg.storage.base import Subgraph
from kg.storage.sqlite import SQLiteAdapter
from kg.traverse import expand


def _load(paths, cfg):
    return SQLiteAdapter(paths.kg_db), make_embedder(cfg)


def search_cli(
    query: str = typer.Argument(...),
    mode: str = typer.Option("hybrid", "--mode"),
    type_filter: str = typer.Option(None, "--type"),
    k: int = typer.Option(10, "-k"),
) -> None:
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    ad, emb = _load(paths, cfg)
    for nid, score in hybrid_search(ad, emb, query, mode=mode, k=k,
                                    type_filter=type_filter, config=cfg):
        n = ad.get(nid)
        typer.echo(f"{score:.4f}  {nid}  {n.name if n else ''}")


def expand_cli(
    ids: list[str] = typer.Argument(...),
    hops: int = typer.Option(2, "--hops"),
) -> None:
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    ad = SQLiteAdapter(paths.kg_db)
    sg = expand(ad, ids, hops=hops, cap=cfg.query.subgraph_cap)
    typer.echo(f"nodes: {len(sg.nodes)}  edges: {len(sg.edges)}")
    for n in sg.nodes:
        typer.echo(f"  {n.id}  {n.name}")


def pack_cli() -> None:
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    raw = json.loads(sys.stdin.read())
    sg = Subgraph(
        nodes=[Node.model_validate(n) for n in raw.get("nodes", [])],
        edges=[Edge.model_validate(e) for e in raw.get("edges", [])],
    )
    typer.echo(pack(sg, seeds={}, rrf_scores=raw.get("rrf_scores"),
                     budget_tokens=raw.get("budget_tokens", 4000)))
