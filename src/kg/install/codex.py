"""Codex installer: AGENTS.md skills + wholly-owned `.codex/config.toml`.

Schema verified against Codex official docs (developers.openai.com/codex):
- `[mcp_servers.<name>]` table with `command` (string) and `args` (array).
- Skills discovered under `$REPO_ROOT/.agents/skills/<name>/SKILL.md`.
- AGENTS.md instruction file at repo root.

Safety contract:
- `.codex/config.toml` is written ONLY when absent. Pre-existing non-empty
  TOML is refused with a manual patch plan — we never rewrite arbitrary TOML
  (Python stdlib has no TOML writer; round-tripping via tomllib would lose
  comments, formatting, and unknown keys).
- `.agents/skills/<name>/` written only when absent (no overwrite without force).
- AGENTS.md uses unique owned markers between which we splice our stanza.
- All writes go through `atomic_write` (same-dir temp + fsync + mode preserved).
- Default dry-run; `apply_plan` is the only write boundary.

ponytail: no TOML patcher for pre-existing configs. When users have an existing
`.codex/config.toml`, we emit a manual patch plan. Add a constrained TOML
editor only if a verified schema-stable patcher is required.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from . import common
from .common import InstallConflict, InstallPlan, ManualStep, PlannedWrite
from .manifest import ArtifactKind, Harness


CODEX_CONFIG_REL = Path(".codex/config.toml")
CODEX_SKILLS_REL = Path(".agents/skills")
CODEX_AGENTS_REL = Path("AGENTS.md")

CODEX_CONFIG_TEMPLATE = """\
# Managed by kg install codex. Replace the file to disable.
[mcp_servers.kg]
command = "kg"
args = ["mcp", "serve", "--project-root", {project}]
"""


def _render_config(project: Path) -> str:
    return CODEX_CONFIG_TEMPLATE.format(project=json.dumps(str(project)))


def _codex_config_bytes(project: Path) -> bytes:
    return _render_config(project).encode("utf-8")


def _render_manual_patch(project: Path) -> str:
    return "Add the following table to .codex/config.toml manually:\n\n" + _render_config(project)


def plan_codex_install(project_root: Path, home_root: Path, *, skills_src: Path | None = None) -> InstallPlan:
    project, _ = common.validate_roots(project_root, home_root)
    source = Path(skills_src or project / "skills").resolve(strict=True)
    plan = InstallPlan(harness=Harness.CODEX, project_root=project, home_root=project,
                       existing_manifest=common.owned_manifest(project, Harness.CODEX))

    config_path = project / CODEX_CONFIG_REL
    common.validate_target(config_path, project)
    if config_path.exists():
        if plan.existing_manifest and config_path.is_file() and not config_path.is_symlink() and config_path.read_bytes() == _codex_config_bytes(project):
            pass
        elif config_path.is_symlink() or not config_path.is_file():
            raise InstallConflict(f"Refusing unsafe pre-existing config: {config_path}")
        else:
            raw = config_path.read_bytes()
            try:
                data = tomllib.loads(raw.decode("utf-8"))
            except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
                raise InstallConflict(
                    f"Malformed pre-existing TOML refused: {config_path}: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise InstallConflict(f"TOML top-level must be a table: {config_path}")
            servers = data.get("mcp_servers", {})
            if not isinstance(servers, dict):
                raise InstallConflict(f"mcp_servers must be a table: {config_path}")
            existing = servers.get("kg", common._MISSING)
            if existing is not common._MISSING:
                raise InstallConflict(
                    f"MCP server name collision at 'kg': {config_path}"
                )
            plan.manual_steps.append(
                ManualStep(config_path, "Pre-existing TOML not rewritten", _render_manual_patch(project))
            )
    else:
        rendered = _codex_config_bytes(project)
        common.add_owned_file(
            plan, config_path, rendered, "kg-install:codex:config"
        )

    common.add_skills(plan, source, project / CODEX_SKILLS_REL)
    common.add_marker(plan, project / CODEX_AGENTS_REL)
    return common.finish_plan(plan)


def apply_plan(plan: InstallPlan):
    return common.apply_plan(plan)


def uninstall(manifest, project_root):
    return common.uninstall(manifest, Harness.CODEX, project_root)
