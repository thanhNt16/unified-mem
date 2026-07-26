"""kg install CLI: per-harness plan / apply / uninstall dispatcher.

Default is DRY-RUN: prints a deterministic plan (paths, fragments, manual
steps, conflicts) and writes nothing. ``--apply`` invokes the harness
``apply_plan`` (sole write boundary). ``--uninstall`` loads the committed
manifest and removes exact owned fragments; drift raises an actionable error
and exits nonzero. ``--force`` is forwarded only to harnesses that support
conflict override (claude).

ponytail: no progress streaming or interactive confirm. Add when a non-CLI
caller needs it; for now stdout text is the only surface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from kg.install import agents_md as agents_inst
from kg.install import claude as claude_inst
from kg.install import codex as codex_inst
from kg.install import cursor as cursor_inst
from kg.install import opencode as opencode_inst
from kg.install.common import InstallConflict, InstallPlan
from kg.install.manifest import Harness, load_manifest, manifest_path

install_app = typer.Typer(
    help="Plan, apply, or uninstall kg harness integrations.",
    no_args_is_help=True,
)

_PLANNERS = {
    Harness.CLAUDE: claude_inst.plan_claude_install,
    Harness.CODEX: codex_inst.plan_codex_install,
    Harness.OPENCODE: opencode_inst.plan_opencode_install,
    Harness.CURSOR: cursor_inst.plan_cursor_install,
    Harness.AGENTS: agents_inst.plan_agents_install,
}
_APPLIERS = {
    Harness.CLAUDE: claude_inst.apply_plan,
    Harness.CODEX: codex_inst.apply_plan,
    Harness.OPENCODE: opencode_inst.apply_plan,
    Harness.CURSOR: cursor_inst.apply_plan,
    Harness.AGENTS: agents_inst.apply_plan,
}


def _uninstall(harness: Harness, manifest, *, force: bool) -> None:
    if harness is Harness.CLAUDE:
        claude_inst.uninstall(manifest, force=force)
    else:
        # Other harnesses share the common uninstaller signature (no force).
        {
            Harness.CODEX: codex_inst.uninstall,
            Harness.OPENCODE: opencode_inst.uninstall,
            Harness.CURSOR: cursor_inst.uninstall,
            Harness.AGENTS: agents_inst.uninstall,
        }[harness](manifest)


def _resolve_roots(
    project_root: Optional[Path], home_root: Optional[Path]
) -> tuple[Path, Path]:
    project = (Path(project_root) if project_root else Path.cwd()).resolve()
    home = (Path(home_root) if home_root else Path.home()).resolve()
    if not project.is_dir():
        raise typer.BadParameter(f"project_root is not a directory: {project}")
    if not (project / ".kg").is_dir():
        raise typer.BadParameter(f"project_root must contain .kg/: {project}")
    if not home.is_dir():
        raise typer.BadParameter(f"home_root is not a directory: {home}")
    return project, home


def _print_plan(name: str, plan) -> None:
    typer.echo(f"# plan: {name}")
    typer.echo(f"project_root: {plan.project_root}")
    typer.echo(f"home_root: {plan.home_root}")
    writes = getattr(plan, "writes", [])
    if writes:
        typer.echo("writes:")
        for w in writes:
            fp = f" fingerprint={w.fingerprint}" if w.fingerprint else ""
            mk = f" marker={w.ownership_marker}" if w.ownership_marker else ""
            typer.echo(f"  - {w.path} [{w.kind.value}]{fp}{mk}")
    skills = getattr(plan, "skills", [])
    if skills:
        typer.echo("skills:")
        for s in skills:
            typer.echo(f"  - {s.source} -> {s.destination}")
    manual = getattr(plan, "manual_steps", [])
    if manual:
        typer.echo("manual_steps:")
        for m in manual:
            typer.echo(f"  - {m.path}: {m.reason}")
            typer.echo(m.patch.rstrip())
    existing = getattr(plan, "existing_manifest", None)
    if not writes and not skills and not manual:
        typer.echo(
            "# already installed; nothing to do"
            if existing
            else "# (no writes planned)"
        )


def _do_one(
    harness: Harness,
    project: Path,
    home: Path,
    skills_src: Optional[Path],
    apply: bool,
    force: bool,
    uninstall: bool,
) -> int:
    name = harness.value
    try:
        if uninstall:
            manifest = load_manifest(project)
            if manifest.harness is not harness:
                typer.echo(
                    f"error [{name}]: manifest at {project} belongs to "
                    f"{manifest.harness.value}, not {name}",
                    err=True,
                )
                return 2
            _uninstall(harness, manifest, force=force)
            typer.echo(f"uninstalled: {name} (manifest removed)")
            return 0
        kwargs: dict = {}
        if skills_src is not None:
            kwargs["skills_src"] = skills_src
        if harness is Harness.CLAUDE:
            kwargs["force"] = force
        plan = _PLANNERS[harness](project, home, **kwargs)
        _print_plan(name, plan)
        if apply:
            manifest = _APPLIERS[harness](plan)
            typer.echo(f"applied: {name} -> {manifest_path(project)}")
        return 0
    except InstallConflict as exc:
        typer.echo(f"error [{name}]: {exc}", err=True)
        return 1
    except FileNotFoundError as exc:
        typer.echo(f"error [{name}]: {exc}", err=True)
        return 2


@install_app.callback(invoke_without_command=True)
def install(
    ctx: typer.Context,
    harness: str = typer.Argument(
        ...,
        help="Harness: claude, codex, opencode, cursor, agents, or all.",
    ),
    project_root: Optional[Path] = typer.Option(
        None, "--project-root", help="Project root (default: cwd)."
    ),
    home_root: Optional[Path] = typer.Option(
        None, "--home-root", help="Home root (default: $HOME)."
    ),
    skills_src: Optional[Path] = typer.Option(
        None, "--skills-src", help="Skills source dir (default: <project>/skills)."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Perform writes (default: dry-run)."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Conflict override; honored by claude only. Never bypasses drift checks.",
    ),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Load manifest and remove owned fragments."
    ),
) -> None:
    """Plan (default), apply, or uninstall kg integrations per harness."""
    if ctx.invoked_subcommand is not None:
        return
    if apply and uninstall:
        typer.echo("error: --apply and --uninstall are mutually exclusive", err=True)
        raise typer.Exit(2)
    project, home = _resolve_roots(project_root, home_root)
    if skills_src is not None:
        skills_src = skills_src.resolve()
        if not skills_src.is_dir():
            typer.echo(
                f"error: skills_src is not a directory: {skills_src}", err=True
            )
            raise typer.Exit(2)

    if harness == "all":
        if uninstall:
            typer.echo(
                "error [all]: per-harness manifests share one project path; "
                "uninstall an individual harness.",
                err=True,
            )
            raise typer.Exit(2)
        # Preflight every plan before any apply. Existing installer manifests are
        # one-per-project, so all cannot safely commit more than one harness.
        # Detect that design conflict before the first write rather than leaving
        # a partially installed project.
        plans = []
        try:
            for h, planner in _PLANNERS.items():
                kwargs = {"skills_src": skills_src} if skills_src is not None else {}
                if h is Harness.CLAUDE:
                    kwargs["force"] = force
                plan = planner(project, home, **kwargs)
                plans.append((h, plan))
                _print_plan(h.value, plan)
        except InstallConflict as exc:
            typer.echo(f"error [all]: {exc}", err=True)
            raise typer.Exit(1)
        if apply:
            typer.echo(
                "error [all]: installers use one .kg-install-manifest.json per "
                "project; applying all would overwrite ownership. Apply an "
                "individual harness.",
                err=True,
            )
            raise typer.Exit(1)
        raise typer.Exit(0)

    try:
        target = Harness(harness)
    except ValueError:
        typer.echo(
            f"error: unknown harness {harness!r}. One of: "
            "claude, codex, opencode, cursor, agents, all.",
            err=True,
        )
        raise typer.Exit(2)
    raise typer.Exit(_do_one(target, project, home, skills_src, apply, force, uninstall))
