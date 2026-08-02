"""kg index — incremental structural source indexing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import typer

from kg.code_index import project_code_file
from kg.config import Config
from kg.coverage import CoverageReport
from kg.file_hashes import FileHashRegistry
from kg.index import IndexMode, plan_index
from kg.paths import KgPaths
from kg.rebuild import RebuildCandidate
from kg.storage.sqlite import SQLiteAdapter

index_app = typer.Typer(help="Incremental source indexing.")


def _paths(root: Path) -> KgPaths:
    root = Path(root).resolve()
    return KgPaths.for_root(root / ".kg")


def index_project(root: Path, project_name: str) -> None:
    """Index a project using the production moderate mode boundary."""
    _project(_paths(root), Path(root).resolve(), "moderate", project_name)


def _project(paths: KgPaths, root: Path, mode: str, project_name: str | None = None) -> CoverageReport:
    cfg = Config.from_path(paths.config)
    plan = plan_index(paths, root, mode, "tree-sitter-v1",
                      incremental_threshold=cfg.index.incremental_threshold)
    report = CoverageReport(generation=SQLiteAdapter(paths.kg_db).generation(), mode=mode)
    registry = FileHashRegistry.load(paths.file_hashes)
    adapter = SQLiteAdapter(paths.kg_db)
    try:
        for record in plan.changes.unchanged:
            report.record(record.rel_path, "skipped", "unchanged")
        for record in (*plan.changes.new, *plan.changes.changed):
            source = root / record.rel_path
            if record.kind not in {"python", "typescript"}:
                report.record(record.rel_path, "skipped", "unsupported")
                continue
            try:
                projection = project_code_file(source, project_name or paths.root.name, record.sha256, "tree-sitter-v1")
                nodes = [node.to_ontology() for node in projection.nodes]
                edges = [edge.to_ontology() for edge in projection.edges
                         if edge.to_id.startswith("code:")]
                adapter.upsert_nodes(nodes)
                adapter.upsert_edges(edges)
                report.record(record.rel_path, "indexed", outputs=[n.id for n in nodes] + [f"edge:{e.id}" for e in edges])
            except Exception as exc:
                report.record(record.rel_path, "failed", type(exc).__name__)
        for record in plan.changes.deleted:
            report.record(record.rel_path, "stale", "deleted")
        registry.apply(plan.changes, "tree-sitter-v1")
        registry.write_atomic(paths.file_hashes)
        report.write_atomic(paths.coverage)
    finally:
        adapter.conn.close()
    return report


@index_app.command("scan")
def scan(path: Path, mode: str = typer.Option("moderate", "--mode")) -> None:
    """Scan a repository; skip unchanged sources."""
    paths = _paths(path)
    report = _project(paths, Path(path).resolve(), mode, paths.root.name)
    body = report.body()["sources"]
    typer.echo(" ".join(f"{key}: {value}" for key, value in body.items() if value))


@index_app.command("code")
def code(path: Path, mode: str = typer.Option("fast", "--mode")) -> None:
    """Code-only alias for scan."""
    scan(path, mode)


@index_app.command("status")
def status(path: Path = typer.Argument(Path("."))) -> None:
    """Show last coverage counts."""
    coverage = _paths(path).coverage
    if not coverage.exists():
        typer.echo("No index coverage. Run `kg index scan <path>`.")
        raise typer.Exit(1)
    body = json.loads(coverage.read_text())
    typer.echo(" ".join(f"{key}: {value}" for key, value in body["sources"].items()))


@index_app.command("rebuild")
def rebuild(path: Path = typer.Argument(Path(".")), full: bool = typer.Option(False, "--full")) -> None:
    """Create and validate a staged rebuild candidate."""
    paths = _paths(path)
    candidate = RebuildCandidate.create(paths, incremental=not full)
    candidate.validate()
    candidate.publish()
    typer.echo("Rebuild published.")


@index_app.command("recover")
def recover(path: Path = typer.Argument(Path("."))) -> None:
    """List retained rebuild candidates for manual recovery."""
    paths = _paths(path)
    candidates = sorted(paths.root.glob(".rebuild-*"))
    typer.echo("\n".join(str(item) for item in candidates) or "No rebuild candidates.")
