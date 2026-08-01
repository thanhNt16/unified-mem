"""Safe, transactional Pi installer contract."""
from __future__ import annotations

from pathlib import Path

from . import common
from .common import InstallPlan
from .manifest import Harness


def plan_pi_install(
    project_root: Path, home_root: Path, *, skills_src: Path | None = None
) -> InstallPlan:
    """Build a read-only plan for project-local Pi artifacts."""
    project, home = common.validate_roots(project_root, home_root)
    plan = InstallPlan(
        Harness.PI,
        project,
        home,
        existing_manifest=common.owned_manifest(project, Harness.PI),
    )
    common.add_skills(
        plan,
        skills_src or common.default_skills_src(project),
        project / ".pi" / "skills",
    )
    common.add_owned_file(
        plan,
        project / ".pi" / "extensions" / "kg.ts",
        _EXTENSION_SOURCE.encode(),
        "kg-install:pi:extension",
    )
    common.add_marker(plan, project / "AGENTS.md")
    return common.finish_plan(plan)


def apply_plan(plan):
    return common.apply_plan(plan)


def uninstall(manifest, project_root):
    return common.uninstall(manifest, Harness.PI, project_root)
