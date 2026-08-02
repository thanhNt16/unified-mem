# Pi Harness Installation Implementation Plan

> **Status: paused / partially implemented.** Task 1 shipped; remaining tasks incomplete. Do not treat this plan as complete.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `kg install pi` to install Pi-local kg CLI tools, skills, context, and opt-out session capture.

**Architecture:** Add a Pi installer following the existing manifest-backed installers. It copies bundled skills into `.pi/skills`, writes a project-local TypeScript extension which exposes narrow `kg` CLI wrappers and captures session text at `session_shutdown`, adds a Pi marker to `AGENTS.md`, then records every artifact in the existing manifest. No MCP server or npm dependency is introduced.

**Tech Stack:** Python 3.11+, Typer, existing `kg.install` manifest transaction layer, Pi project-local TypeScript extensions, Node `node:child_process`, TypeBox already provided by Pi.

## Global Constraints

- `kg install pi` is dry-run by default; `--apply` is the only write path.
- Integration is project-scoped. All owned files remain under project root.
- Do not install Pi, npm packages, or configure MCP.
- Do not mutate user-owned `.pi/settings.json`; configuration lives in owned `.pi/extensions/kg.ts`.
- Pi tools invoke only the installed `kg` executable from the current project cwd.
- Capture defaults on. `/kg-capture off` persists opt-out in Pi session custom state; `/kg-capture on` re-enables it.
- Capture only active-branch `user`/`assistant` text. Redact with `kg.conversation.redact_secrets` by passing a generated JSONL transcript to `kg raw add - --type text --title ... --conversation`; raw ingestion remains the sole writer.
- Abort or nonzero `kg` exits return tool errors. Session-capture failures notify in TUI only; shutdown never fails.
- Test extension behavior with Node built-ins only. Python installer tests own `tests/test_install_pi.py`.

---

## File Structure

- Create: `src/kg/install/pi.py` — Pi-specific install plan, owned extension source, manifest apply/uninstall adapters.
- Modify: `src/kg/install/manifest.py` — add `Harness.PI`.
- Modify: `src/kg/cli/install_cli.py` — register Pi planner/applier/uninstaller and expose Pi in validation/help.
- Create: `tests/test_install_pi.py` — installer safety, artifacts, idempotence, uninstall tests.
- Modify: `tests/cli/test_install_cli.py` — CLI dry-run/apply dispatch coverage for `pi`.
- Modify: `README.md` — list Pi in supported harnesses and setup command.
- Modify: `docs/guides/harness-setup.md` — Pi setup, trust, tool/skill/capture behavior, disable command.

## Task 1: Add Pi installer contract

**Files:**
- Modify: `src/kg/install/manifest.py:35-42`
- Create: `src/kg/install/pi.py`
- Test: `tests/test_install_pi.py`

**Interfaces:**
- Consumes: `common.InstallPlan`, `common.add_skills`, `common.add_marker`, `common.add_owned_file`, `common.finish_plan`, `common.apply_plan`, `common.uninstall`.
- Produces: `plan_pi_install(project_root: Path, home_root: Path, *, skills_src: Path | None = None) -> InstallPlan`, `apply_plan(plan)`, `uninstall(manifest, project_root)`.

- [ ] **Step 1: Write failing installer tests**

```python
from kg.install import common
from kg.install.manifest import Harness, load_manifest
from kg.install.pi import plan_pi_install, uninstall


def test_plan_pi_owns_extension_skills_and_context(tmp_path):
    home, project = roots(tmp_path)
    planned = plan_pi_install(project, home)
    assert planned.harness is Harness.PI
    assert {s.destination.relative_to(project).as_posix() for s in planned.skills} == {
        ".pi/skills/kg-ingest", ".pi/skills/kg-extract",
        ".pi/skills/kg-query", ".pi/skills/kg-dream",
    }
    assert {w.path.relative_to(project).as_posix() for w in planned.writes} == {
        ".pi/extensions/kg.ts", "AGENTS.md",
    }


def test_apply_pi_records_manifest_and_uninstall_preserves_agents(tmp_path):
    home, project = roots(tmp_path)
    (project / "AGENTS.md").write_text("# User rules\n")
    manifest = common.apply_plan(plan_pi_install(project, home))
    assert load_manifest(project).harness is Harness.PI
    assert (project / ".pi/extensions/kg.ts").is_file()
    uninstall(manifest, project)
    assert (project / "AGENTS.md").read_text() == "# User rules\n"
    assert not (project / ".pi").exists()
```

- [ ] **Step 2: Run tests, verify failure**

Run: `uv run pytest tests/test_install_pi.py -q`

Expected: FAIL during collection: `ModuleNotFoundError: No module named 'kg.install.pi'`.

- [ ] **Step 3: Add enum and minimal installer**

```python
# src/kg/install/manifest.py
class Harness(str, Enum):
    # existing members
    PI = "pi"
```

```python
# src/kg/install/pi.py
from pathlib import Path
from . import common
from .common import InstallPlan
from .manifest import Harness


def plan_pi_install(project_root: Path, home_root: Path, *, skills_src: Path | None = None) -> InstallPlan:
    project, home = common.validate_roots(project_root, home_root)
    plan = InstallPlan(Harness.PI, project, home, existing_manifest=common.owned_manifest(project, Harness.PI))
    common.add_skills(plan, skills_src or common.default_skills_src(project), project / ".pi" / "skills")
    common.add_owned_file(plan, project / ".pi" / "extensions" / "kg.ts", _EXTENSION_SOURCE.encode(), "kg-install:pi:extension")
    common.add_marker(plan, project / "AGENTS.md")
    return common.finish_plan(plan)


def apply_plan(plan):
    return common.apply_plan(plan)


def uninstall(manifest, project_root):
    return common.uninstall(manifest, Harness.PI, project_root)
```

Define `_EXTENSION_SOURCE` in this file in Task 2. Keep owned directory cleanup out of scope: the common uninstaller removes files/skills, leaving empty `.pi` directories if present.

- [ ] **Step 4: Run installer tests**

Run: `uv run pytest tests/test_install_pi.py -q`

Expected: PASS after Task 2 adds `_EXTENSION_SOURCE`.

- [ ] **Step 5: Commit**

```bash
git add src/kg/install/manifest.py src/kg/install/pi.py tests/test_install_pi.py
git commit -m "feat: add Pi installer skeleton"
```

## Task 2: Implement Pi CLI extension

**Files:**
- Modify: `src/kg/install/pi.py`
- Test: `tests/test_install_pi.py`

**Interfaces:**
- Consumes: Pi `ExtensionAPI`, `Type` from `typebox`, `ctx.cwd`, `ctx.sessionManager.buildContextEntries()`, `session_shutdown`.
- Produces: tools `kg_query`, `kg_search`, `kg_ingest`; command `/kg-capture on|off|status`; automatic conversation ingest.

- [ ] **Step 1: Write extension-content assertions**

```python
def test_pi_extension_has_only_cli_tools_and_capture_controls(tmp_path):
    home, project = roots(tmp_path)
    plan = plan_pi_install(project, home)
    extension = next(w.data.decode() for w in plan.writes if w.path.name == "kg.ts")
    assert 'name: "kg_query"' in extension
    assert 'name: "kg_search"' in extension
    assert 'name: "kg_ingest"' in extension
    assert 'pi.registerCommand("kg-capture"' in extension
    assert 'pi.on("session_shutdown"' in extension
    assert 'spawn("kg"' in extension
    assert 'shell: false' in extension
    assert "mcp" not in extension.lower()
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/test_install_pi.py::test_pi_extension_has_only_cli_tools_and_capture_controls -q`

Expected: FAIL because `_EXTENSION_SOURCE` does not exist.

- [ ] **Step 3: Implement the extension string**

Embed this minimal extension in `_EXTENSION_SOURCE`. Use `spawn` argument arrays; never use a shell. Bound returned output to 100,000 characters.

```typescript
import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

type KgResult = { stdout: string; stderr: string; exitCode: number };
const LIMIT = 100_000;

function runKg(cwd: string, args: string[], input?: string): Promise<KgResult> {
  return new Promise((resolve, reject) => {
    const child = spawn("kg", args, { cwd, shell: false, stdio: "pipe" });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout = (stdout + chunk).slice(-LIMIT); });
    child.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-LIMIT); });
    child.on("error", reject);
    child.on("close", (exitCode) => resolve({ stdout, stderr, exitCode: exitCode ?? 1 }));
    if (input) child.stdin.end(input); else child.stdin.end();
  });
}

function result(output: KgResult) {
  const text = output.stdout || output.stderr || `kg exited ${output.exitCode}`;
  return { content: [{ type: "text" as const, text }], details: { exitCode: output.exitCode }, isError: output.exitCode !== 0 };
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "kg_query", label: "KG Query",
    description: "Retrieve packed local graph context for a natural-language question.",
    parameters: Type.Object({ query: Type.String(), budgetTokens: Type.Optional(Type.Integer({ minimum: 1 })), mode: Type.Optional(Type.Union([Type.Literal("find"), Type.Literal("trace"), Type.Literal("explain")])) }),
    async execute(_id, params, _signal, _update, ctx) {
      const args = ["query", params.query];
      if (params.budgetTokens) args.push("--budget-tokens", String(params.budgetTokens));
      if (params.mode) args.push("--mode", params.mode);
      return result(await runKg(ctx.cwd, args));
    },
  });
  // Register kg_search with ["search", params.query, "-k", String(params.limit)].
  // Register kg_ingest with ["raw", "add", "-", "--type", "text", "--title", params.title] and params.content as stdin.
  // Implement capture state and shutdown in Step 3 below.
}
```

Complete extension behavior:

- `kg_search`: parameters `query: string`, optional `limit: integer` default 10, range 1–50; invokes `kg search <query> -k <limit>`.
- `kg_ingest`: parameters `content: string` (min length 1), optional `title: string`; invokes `kg raw add - --type text` plus `--title <title>` only when supplied. This is explicit manual source ingestion, not graph extraction.
- `/kg-capture`: exact args only `on`, `off`, `status`. Persist `{ capture: boolean }` with `ctx.sessionManager.appendCustomEntry("kg", { capture })`. Resolve latest `custom` entry with `customType === "kg"`; default `true`.
- `session_shutdown`: return immediately if capture disabled, session is ephemeral, or active branch has no text. Convert only active-branch entries whose `message.role` is `user` or `assistant` to JSONL records `{ role, content }`. For content arrays retain `type === "text"` blocks only. Write no temporary transcript: call `runKg(ctx.cwd, ["raw", "add", "-", "--type", "text", "--title", `Pi session ${ctx.sessionManager.getSessionId()}`, "--conversation"], jsonl)`.
- Never include tool input/output, thinking blocks, images, custom entries, or session metadata in capture.
- On capture failure, call `ctx.ui.notify("kg session capture failed: ...", "error")` only when `ctx.hasUI`; do not throw.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_install_pi.py -q`

Expected: PASS.

- [ ] **Step 5: Typecheck generated extension against Pi runtime**

Run:

```bash
mkdir -p /tmp/kg-pi-extension-check
python - <<'PY'
from pathlib import Path
from kg.install.pi import _EXTENSION_SOURCE
Path('/tmp/kg-pi-extension-check/kg.ts').write_text(_EXTENSION_SOURCE)
PY
cd /tmp/kg-pi-extension-check
pi --no-session --no-context-files --extension ./kg.ts --tools kg_query -p "Call kg_query with query test." || true
```

Expected: Pi loads `kg.ts`; failure may report missing `.kg` or unavailable `kg`, never TypeScript/import/schema errors.

- [ ] **Step 6: Commit**

```bash
git add src/kg/install/pi.py tests/test_install_pi.py
git commit -m "feat: add Pi kg CLI extension"
```

## Task 3: Wire `kg install pi` CLI

**Files:**
- Modify: `src/kg/cli/install_cli.py:16-55, 116-180`
- Modify: `tests/cli/test_install_cli.py`

**Interfaces:**
- Consumes: `Harness.PI`, `pi_inst.plan_pi_install`, `pi_inst.apply_plan`, `pi_inst.uninstall`.
- Produces: valid CLI target `pi`, normal dry-run/apply/uninstall behavior.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_install_pi_dry_run_lists_extension_and_skills(runner, project, home):
    result = runner.invoke(app, ["install", "pi", "--project-root", str(project), "--home-root", str(home)])
    assert result.exit_code == 0
    assert ".pi/extensions/kg.ts" in result.stdout
    assert ".pi/skills/kg-query" in result.stdout


def test_install_pi_apply_and_uninstall(runner, project, home):
    applied = runner.invoke(app, ["install", "pi", "--project-root", str(project), "--home-root", str(home), "--apply"])
    assert applied.exit_code == 0
    removed = runner.invoke(app, ["install", "pi", "--project-root", str(project), "--home-root", str(home), "--uninstall"])
    assert removed.exit_code == 0
```

Use existing test fixtures/import style in `tests/cli/test_install_cli.py`; preserve its project `.kg` setup.

- [ ] **Step 2: Run CLI tests, verify failure**

Run: `uv run pytest tests/cli/test_install_cli.py -q`

Expected: FAIL with unknown harness `pi`.

- [ ] **Step 3: Register Pi adapter**

```python
from kg.install import pi as pi_inst

_PLANNERS[Harness.PI] = pi_inst.plan_pi_install
_APPLIERS[Harness.PI] = pi_inst.apply_plan
```

Add `Harness.PI: pi_inst.uninstall` to `_uninstall` map. Extend `install()` help string and unknown-harness error list with `pi`. Do not add `allow_writes` logic for Pi.

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/cli/test_install_cli.py tests/test_install_pi.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kg/cli/install_cli.py tests/cli/test_install_cli.py
git commit -m "feat: expose Pi installer command"
```

## Task 4: Document Pi setup

**Files:**
- Modify: `README.md:1-55`
- Modify: `docs/guides/harness-setup.md`
- Test: `tests/test_docs.py`

**Interfaces:**
- Consumes: actual `kg install pi` contract.
- Produces: copy-paste Pi install and usage instructions.

- [ ] **Step 1: Add documentation assertions if current docs tests require explicit links**

Inspect `tests/test_docs.py`. If it verifies README command lists, add assertions matching the project’s style:

```python
assert "kg install pi --apply" in readme
assert "Pi" in harness_setup
```

- [ ] **Step 2: Run docs test, verify expected failure or identify absent coverage**

Run: `uv run pytest tests/test_docs.py -q`

Expected: either FAIL before docs update, or PASS with no existing assertion. Do not add a test solely for prose when the suite has no docs-content convention.

- [ ] **Step 3: Update documentation**

README supported-harness text:

```markdown
Pi: `kg install pi --apply` installs local skills, the `kg` CLI extension, and optional-by-default session capture. Pi itself is not installed by `kg`.
```

Harness guide Pi section:

```markdown
## Pi

```bash
kg install pi --apply
pi
```

Trust this project when Pi prompts; it loads `.pi/extensions/kg.ts` and `.pi/skills/` only in trusted projects. The extension exposes `kg_query`, `kg_search`, and `kg_ingest`, each backed by the local `kg` CLI. It captures active-branch user/assistant text at session shutdown, redacting secrets through kg’s conversation pipeline. Disable for the current/future Pi session with `/kg-capture off`; restore with `/kg-capture on`; inspect with `/kg-capture status`.

`kg install pi` never installs Pi, npm packages, or an MCP server.
```

- [ ] **Step 4: Run docs tests**

Run: `uv run pytest tests/test_docs.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/guides/harness-setup.md tests/test_docs.py
git commit -m "docs: document Pi integration"
```

## Task 5: Full verification

**Files:**
- Verify only.

- [ ] **Step 1: Run Pi installer lifecycle in a disposable initialized project**

```bash
sandbox=$(mktemp -d)
uv run kg init --project-root "$sandbox" --user-id test --scope test
uv run kg install pi --project-root "$sandbox" --home-root "$HOME"
uv run kg install pi --project-root "$sandbox" --home-root "$HOME" --apply
find "$sandbox/.pi" -maxdepth 3 -type f | sort
uv run kg install pi --project-root "$sandbox" --home-root "$HOME" --uninstall
rm -rf "$sandbox"
```

Expected: dry-run lists `.pi/extensions/kg.ts` and four skills; apply creates them plus manifest; uninstall succeeds without removing non-owned content.

- [ ] **Step 2: Run focused tests**

Run: `uv run pytest tests/test_install_pi.py tests/cli/test_install_cli.py tests/test_docs.py -q`

Expected: PASS.

- [ ] **Step 3: Run full suite**

Run: `uv run pytest`

Expected: all existing tests plus Pi tests pass; only pre-existing skipped tests remain skipped.

- [ ] **Step 4: Review final diff**

Run: `git diff --check && git status --short && git log --oneline -4`

Expected: no whitespace errors; only intended tracked changes; four focused commits present.

- [ ] **Step 5: Commit any verification-only corrections**

```bash
git add <corrected-files>
git commit -m "fix: correct Pi integration verification"
```

Run only if verification found a real defect. Otherwise no commit.

## Plan Self-Review

- Spec coverage: installer, Pi CLI tools, project-local skills/context, default session capture, persisted opt-out, redaction pipeline, error behavior, docs, lifecycle tests covered by Tasks 1–5.
- No-MCP/no-install constraint: explicit global constraints and extension assertions.
- Type consistency: installer exports and registered tool/command names are identical across tasks.
- Scope: no extension packaging, MCP, Pi installation, temporary-file transcript format, or graph extraction automation.
