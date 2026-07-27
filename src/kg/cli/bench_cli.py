"""kg bench — deterministic benchmark runner."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

VALID_SCALES = (10, 50, 250)


def _print_summary(report: dict) -> None:
    tiers = report.get("tiers", {})
    lines = ["Benchmark summary:", ""]
    for scale, tier in tiers.items():
        status = tier.get("status", "UNKNOWN")
        lines.append(f"  scale {scale}: {status}")
        if status == "measured":
            r = tier.get("result", {})
            if r:
                sp = r.get("speed", {})
                cl = r.get("graph_cleanliness", {})
                lines.append(f"    speed median: {sp.get('median_seconds', '?')}s")
                lines.append(f"    cleanliness score: {cl.get('score', '?')}")
    lines.append(f"")
    lines.append(f"cost: {report.get('cost_reason', 'N/A')}")
    typer.echo("\n".join(lines))


def bench_cli(
    scale: Annotated[
        int,
        typer.Option("--scale", "-s", help="Corpus size: 10, 50, or 250. Default 10 (CI-safe).")
    ] = 10,
    corpus_dir: Annotated[
        Path | None,
        typer.Option("--corpus-dir", help="Use existing corpus dir instead of generating.")
    ] = None,
    out_dir: Annotated[
        Path | None,
        typer.Option("--out-dir", help="Output directory for results.")
    ] = None,
) -> None:
    """Run the deterministic benchmark suite."""
    if scale not in VALID_SCALES:
        typer.echo(f"Error: --scale must be one of {VALID_SCALES}, got {scale}", err=True)
        raise typer.Exit(1)

    today = datetime.now(timezone.utc).date().isoformat()
    if out_dir is None:
        out_dir = Path(f"bench/results/{today}")
    else:
        out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from bench.corpus import make_corpus_at_scale
        from bench.reporter import render_report
        from bench.runners import QUERIES, run_baseline, run_kg
    except ModuleNotFoundError as e:
        typer.echo(
            "Error: the benchmark harness (the `bench` package) is not importable. "
            "`kg bench` is a developer/CI tool that ships with the kg source checkout. "
            "Run it via `make bench` from the repository root, or set PYTHONPATH to the checkout.",
            err=True,
        )
        raise typer.Exit(2) from e

    if corpus_dir is None:
        corpus_base = out_dir / "corpus"
        corpus_base.mkdir(parents=True, exist_ok=True)
        typer.echo(f"Generating {scale}-doc corpus...")
        make_corpus_at_scale(scale, corpus_base)
        corpus_dir = corpus_base / f"scale-{scale}" / "corpus"
    else:
        corpus_dir = Path(corpus_dir)

    if not corpus_dir.is_dir():
        typer.echo(f"Error: corpus dir not found: {corpus_dir}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Running kg benchmark (scale={scale}, fake embedder)...")
    with tempfile.TemporaryDirectory() as tmp:
        kg_result = run_kg(corpus_dir, tmp, scale=scale, embedder_opt="fake")

    typer.echo("Running baseline grep...")
    baseline_results = [run_baseline(corpus_dir, query=q) for q in QUERIES]

    report_path = render_report([kg_result], baseline_results, out_path=out_dir)
    typer.echo(f"Report written to {report_path}")

    # Print summary to stdout
    import json
    report = json.loads(report_path.read_text(encoding="utf-8"))
    _print_summary(report)
