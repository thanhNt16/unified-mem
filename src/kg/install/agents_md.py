"""Harness-neutral AGENTS.md fallback installer.

Only a unique marker block is owned. All surrounding bytes are preserved.
Malformed, duplicate, nested, or drifted markers fail closed. Default planning
is read-only; apply is explicit and atomic.
"""
from __future__ import annotations

from pathlib import Path

from . import common
from .common import InstallConflict, InstallPlan
from .manifest import Harness


def plan_agents_install(project_root: Path, home_root: Path, *, skills_src: Path | None = None) -> InstallPlan:
    del skills_src
    project, home = common.validate_roots(project_root, home_root)
    plan = InstallPlan(Harness.AGENTS, project, home,
                       existing_manifest=common.owned_manifest(project, Harness.AGENTS))
    common.add_marker(plan, project / "AGENTS.md")
    return common.finish_plan(plan)


def apply_plan(plan: InstallPlan): return common.apply_plan(plan)
def uninstall(manifest, project_root): return common.uninstall(manifest, Harness.AGENTS, project_root)
