"""Cursor installer using verified project MCP/rules schemas.

Official schema assumptions (cursor.com/docs/mcp + /docs/rules):
- Project MCP config: `<project>/.cursor/mcp.json`.
- Servers: `mcpServers.<name> = {type: "stdio", command, args}`.
- Project rules: `<project>/.cursor/rules/*.mdc`, YAML frontmatter with
  `alwaysApply` boolean.
- Cursor CLI reads project AGENTS.md. We use that fallback rather than assuming
  Claude-style skill-directory semantics.

Plain JSON is structurally merged. Unknown top-level keys and unrelated MCP
servers survive. Malformed/non-object JSON and `kg` collisions fail closed.
"""
from __future__ import annotations

from pathlib import Path

from . import common
from .common import InstallConflict, InstallPlan
from .manifest import Harness

MCP_REL = Path(".cursor/mcp.json")
RULE_REL = Path(".cursor/rules/kg.mdc")
RULE = b"""---
description: Local kg project-memory workflows
alwaysApply: true
---
Use kg-extract, kg-query, and kg-dream guidance from the project AGENTS.md stanza. Use the project-scoped kg MCP server for memory operations.
"""


def _mcp(project: Path) -> dict:
    argv = common.mcp_argv(project)
    return {"type": "stdio", "command": argv[0], "args": argv[1:]}


def plan_cursor_install(project_root: Path, home_root: Path, *, skills_src: Path | None = None) -> InstallPlan:
    del skills_src  # Cursor skills schema intentionally not assumed; AGENTS.md fallback.
    project, home = common.validate_roots(project_root, home_root)
    plan = InstallPlan(Harness.CURSOR, project, home,
                       existing_manifest=common.owned_manifest(project, Harness.CURSOR))
    common.add_json_mcp(plan, project / MCP_REL, "mcpServers", _mcp(project))
    common.add_owned_file(plan, project / RULE_REL, RULE, "kg-install:cursor:rule")
    common.add_marker(plan, project / "AGENTS.md")
    return common.finish_plan(plan)


def apply_plan(plan: InstallPlan): return common.apply_plan(plan)
def uninstall(manifest): return common.uninstall(manifest, Harness.CURSOR)
