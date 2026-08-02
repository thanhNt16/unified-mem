"""Safe, transactional Pi installer contract."""
from __future__ import annotations

from pathlib import Path

from . import common
from .common import InstallPlan
from .manifest import Harness

# Pi project-local extension (`.pi/extensions/kg.ts`) bridging the `kg` CLI.
# Faithful to the official Pi extension API (default-export factory receiving
# ExtensionAPI; registerTool/registerCommand). Skills + AGENTS.md marker carry
# the workflow; this surfaces `kg` as a callable tool and `/kg` command.
_EXTENSION_SOURCE = """// Installed by `kg install pi`. Bridges the local kg memory CLI into Pi.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { execFileSync } from "node:child_process";

function runKg(args: string): string {
  const argv = args.split(/\\s+/).filter(Boolean);
  return execFileSync("kg", argv, { encoding: "utf-8", maxBuffer: 10 * 1024 * 1024 });
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "kg",
    label: "kg",
    description:
      "Run the local kg memory CLI (e.g. `search`, `save`, `status`). " +
      "Use the kg-ingest/kg-extract/kg-query/kg-dream skills for the workflow.",
    parameters: Type.Object({
      command: Type.String({ description: "kg subcommand and args, e.g. 'search hybrid <query>'" }),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      try {
        return { content: [{ type: "text", text: runKg(params.command) }], details: {} };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return { content: [{ type: "text", text: `kg error: ${msg}` }], details: { error: true } };
      }
    },
  });

  pi.registerCommand("kg", {
    description: "Run a kg memory CLI command",
    handler: async (args, ctx) => {
      try {
        ctx.ui.notify(runKg(args ?? "").trim().slice(0, 200), "info");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        ctx.ui.notify(`kg error: ${msg}`, "error");
      }
    },
  });
}
"""


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
