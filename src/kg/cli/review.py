from __future__ import annotations

import json
from dataclasses import asdict

import typer

from kg.config import Config
from kg.dedup import Deduper
from kg.dream import dream_candidates
from kg.embed import make_embedder
from kg.gate import AuditRecord, Gate
from kg.paths import KgPaths
from kg.resolve import Resolver
from kg.storage.sqlite import SQLiteAdapter


review_app = typer.Typer(help="Review pending same_as candidates.")


def _gate(paths: KgPaths) -> tuple[SQLiteAdapter, Gate]:
    cfg = Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    emb = make_embedder(cfg)
    return adapter, Gate(
        adapter, Resolver(adapter, emb, cfg.thresholds),
        Deduper(adapter, emb, cfg.thresholds), emb, cfg, cfg.project.user_id,
    )


def _append_audit(paths: KgPaths, audit: AuditRecord, reason: str | None) -> None:
    record = {**asdict(audit), "reason": reason}
    try:
        with (paths.wiki / "log.md").open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        typer.echo(f"warning: database committed; lineage log append failed: {exc}", err=True)


def merge_cli(
    winner_id: str = typer.Argument(..., metavar="WINNER_ID"),
    loser_id: str = typer.Argument(..., metavar="LOSER_ID"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    paths = KgPaths.for_cwd()
    _, gate = _gate(paths)
    audit = gate.merge(winner_id, loser_id, reason=reason)
    _append_audit(paths, audit, reason)
    typer.echo(f"merged {loser_id} into {winner_id}")


@review_app.command("list")
def review_list_cli() -> None:
    paths = KgPaths.for_cwd()
    adapter = SQLiteAdapter(paths.kg_db)
    candidates = [c for c in dream_candidates(adapter, since=None) if c.reason == "pending"]
    if not candidates:
        typer.echo("no pending same_as reviews")
        return
    for candidate in candidates:
        typer.echo(
            f"PENDING {candidate.edge_id} score={candidate.score:.2f}"
        )


@review_app.command("confirm")
def review_confirm_cli(
    edge_id: str = typer.Argument(..., metavar="EDGE_ID"),
    winner_id: str = typer.Option(..., "--winner", metavar="NODE_ID"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    paths = KgPaths.for_cwd()
    adapter, gate = _gate(paths)
    row = adapter.conn.execute(
        "SELECT source, target FROM edges WHERE id=?", (edge_id,)
    ).fetchone()
    if row is None or winner_id not in {row["source"], row["target"]}:
        raise typer.BadParameter("winner must be one edge endpoint", param_hint="--winner")
    loser_id = row["target"] if winner_id == row["source"] else row["source"]
    audit = gate.review_merge(edge_id, winner_id, loser_id)
    _append_audit(paths, audit, reason)
    typer.echo(f"confirmed {edge_id}; merged {loser_id} into {winner_id}")


@review_app.command("reject")
def review_reject_cli(
    edge_id: str = typer.Argument(..., metavar="EDGE_ID"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    paths = KgPaths.for_cwd()
    _, gate = _gate(paths)
    audit = gate.reject_review(edge_id)
    _append_audit(paths, audit, reason)
    typer.echo(f"rejected {edge_id}")
