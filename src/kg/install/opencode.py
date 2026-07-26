"""OpenCode installer using the verified project `opencode.json` schema.

Official schema assumptions (opencode.ai/docs/config + /docs/mcp-servers):
- Project config: `<project>/opencode.json` (plain JSON).
- Local MCP: `mcp.kg = {type: "local", command: [argv...], enabled: true}`.
- Skills: `<project>/.opencode/skills/<name>/SKILL.md`.
- Instructions fallback: project AGENTS.md owned marker block.

`opencode.jsonc` is never parsed or reformatted: its comments would be lost by
stdlib json. Presence yields a deterministic manual step and suppresses JSON
config mutation. Plain JSON is structurally merged; unknown keys survive.
"""
from __future__ import annotations

from pathlib import Path

from . import common
from .common import InstallConflict, InstallPlan, ManualStep
from .manifest import Harness

CONFIG_REL = Path("opencode.json")
JSONC_REL = Path("opencode.jsonc")
SKILLS_REL = Path(".opencode/skills")


def _mcp(project: Path) -> dict:
    return {"type": "local", "command": common.mcp_argv(project), "enabled": True}


def _manual(project: Path) -> str:
    import json
    return "Merge this object into opencode.jsonc manually:\n" + json.dumps({"mcp": {"kg": _mcp(project)}}, indent=2)


def plan_opencode_install(project_root: Path, home_root: Path, *, skills_src: Path | None = None) -> InstallPlan:
    project, home = common.validate_roots(project_root, home_root)
    source = Path(skills_src or project / "skills").resolve(strict=True)
    plan = InstallPlan(Harness.OPENCODE, project, home,
                       existing_manifest=common.owned_manifest(project, Harness.OPENCODE))
    config, jsonc = project / CONFIG_REL, project / JSONC_REL
    common.validate_target(jsonc, project)
    if jsonc.exists():
        if jsonc.is_symlink() or not jsonc.is_file():
            raise InstallConflict(f"Refusing unsafe JSONC config: {jsonc}")
        if config.exists():
            raise InstallConflict(f"Both opencode.json and opencode.jsonc exist; precedence ambiguous")
        plan.manual_steps.append(ManualStep(jsonc, "JSONC comments cannot be preserved by stdlib json", _manual(project)))
    else:
        common.add_json_mcp(plan, config, "mcp", _mcp(project))
    common.add_skills(plan, source, project / SKILLS_REL)
    common.add_marker(plan, project / "AGENTS.md")
    return common.finish_plan(plan)


def apply_plan(plan: InstallPlan): return common.apply_plan(plan)
def uninstall(manifest): return common.uninstall(manifest, Harness.OPENCODE)
