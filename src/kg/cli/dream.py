from __future__ import annotations

import json
from dataclasses import asdict

import typer

from kg.config import Config
from kg.dream import dream_candidates
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter


dream_app = typer.Typer(help="Dream review: surface candidates for human judgment.")


def _human_line(candidate) -> str:
    parts = [candidate.reason, f"score={candidate.score:.2f}"]
    if candidate.edge_id:
        parts.append(f"edge={candidate.edge_id}")
    parts.append(f"nodes={' '.join(candidate.node_ids)}")
    if candidate.detail:
        parts.append(candidate.detail)
    return "  ".join(parts)


@dream_app.command("candidates")
def dream_candidates_cli(
    since: str | None = typer.Option(
        None, "--since", help="ISO timestamp or duration (7d, 12h, 30m). Default: all active nodes.",
    ),
    kind: str | None = typer.Option(
        None, "--kind", help="Filter by reason kind (case-insensitive).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON array."),
) -> None:
    paths = KgPaths.for_cwd()
    Config.from_path(paths.config)
    adapter = SQLiteAdapter(paths.kg_db)
    candidates = dream_candidates(adapter, since=since)
    if kind:
        candidates = [c for c in candidates if c.reason.lower() == kind.lower()]
    candidates.sort(key=lambda c: (c.reason, -c.score, tuple(c.node_ids), c.edge_id or ""))
    if json_out:
        typer.echo(json.dumps([asdict(c) for c in candidates], ensure_ascii=False))
        return
    if not candidates:
        typer.echo("no candidates")
        return
    for candidate in candidates:
        typer.echo(_human_line(candidate))


