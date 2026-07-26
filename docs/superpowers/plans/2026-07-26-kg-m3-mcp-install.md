# KG M3 — MCP Server & Harness Installers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) tracking.

**Goal:** Serve `kg` to any harness two ways: (1) a FastMCP stdio server exposing the agent-shaped tool subset + Resources, and (2) `kg install <harness>` that auto-writes skills, the MCP entry, instruction stanzas, and the continual-learning hook into Claude Code / Codex / OpenCode / Cursor (+ `AGENTS.md` fallback). Plus the conversation-ingest hook.

**Architecture:** The MCP server is a thin wrapper — each tool calls the same core functions the CLI uses (M1/M2), never raw DB ops. `kg install` is a set of per-harness *writers* that render config fragments into well-known locations, idempotent and `--uninstall`-reversible. Both are pure plumbing over the engine; no new core logic.

**Tech Stack:** `fastmcp` (or `mcp` SDK) for the stdio server; stdlib JSON/TOML for installer config rendering. Builds on M1/M2 core + the 4 skills (M1/M2). pytest TDD; MCP tested via in-process client.

> **Interface assumption (subject to M1/M2 realized shapes):** tool implementations import `kg.cli.save._build_gate`, `kg.search.hybrid_search`, `kg.dream.dream_candidates`, `kg.raw_ops.add_source`, `kg.wiki.sync_entities`. If those signatures shifted during M1/M2 execution, adapt the calls — the tool *contract* (names below) is what's stable.

## Global Constraints

- **Agent-shaped tools only** (spec §6.1): `ingest_url`, `ingest_file`, `ingest_text`, `ingest_conversation`, `save_pole`, `query_memory`, `nl_query_memory`, `deep_search_memory`, `dream_candidates`, `review_same_as`. Never raw SQL.
- **MCP Resources** expose `wiki/index.md` and `ontology.json` (spec §6.2).
- **`kg install` is diff-friendly + reversible.** Merges into existing config; `--uninstall` removes exactly what it wrote. Records a manifest at `~/.kg-install/<harness>.json`.
- **Skills are markdown + the CLI contract** — the installer copies the `skills/kg-*` dirs; it does not generate skill logic.

---

## File Map

| File | Responsibility |
|---|---|
| `src/kg/mcp/server.py` | FastMCP server: tool + resource registration, thin wrappers over core |
| `src/kg/install/__init__.py` | `install(harness, uninstall)`, manifest read/write |
| `src/kg/install/manifest.py` | `~/.kg-install/<harness>.json` write/read; tracks written paths |
| `src/kg/install/claude_code.py` | write skills dir + `.mcp.json` + `settings.json` SessionEnd hook + `CLAUDE.md` stanza |
| `src/kg/install/codex.py`, `opencode.py`, `cursor.py`, `agents_md.py` | per-harness writers |
| `src/kg/conversation.py` | format a session transcript → markdown for `kg raw add --type conversation` |
| `src/kg/cli/install_cli.py` | `kg install` / `kg install --uninstall` |
| `src/kg/cli/mcp_cli.py` | `kg mcp serve` entrypoint |
| `tests/mcp/test_server.py`, `tests/install/test_*.py`, `tests/test_conversation.py` |

---

### Task 1: `conversation.py` — transcript → markdown

**Files:** Create `src/kg/conversation.py`, `tests/test_conversation.py`
**Interfaces — Produces:** `format_conversation(messages: list[dict], session_id: str, harness: str) -> str` where each message is `{role, content, ts}`. Output is markdown with YAML-ish header comment the SessionEnd hook writes to `raw/conversations/`.

- [ ] **Step 1: Failing test**

```python
# tests/test_conversation.py
from kg.conversation import format_conversation


def test_format_basic():
    md = format_conversation(
        [{"role": "user", "content": "hi", "ts": "2026-07-26T10:00:00Z"},
         {"role": "assistant", "content": "hello", "ts": "2026-07-26T10:00:01Z"}],
        session_id="sess-1", harness="claude-code")
    assert "**user**" in md and "**assistant**" in md
    assert "hi" in md and "hello" in md
    assert "sess-1" in md
```

- [ ] **Step 2: Run FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/conversation.py
from __future__ import annotations


def format_conversation(messages: list[dict], session_id: str, harness: str) -> str:
    lines = [f"<!-- session: {session_id} | harness: {harness} -->", ""]
    for m in messages:
        role = m.get("role", "unknown")
        ts = m.get("ts", "")
        lines.append(f"### **{role}** `{ts}`")
        lines.append("")
        lines.append(m.get("content", ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run PASS**
- [ ] **Step 5: Commit** — `feat(m3): conversation transcript → markdown`

---

### Task 2: Install manifest

**Files:** Create `src/kg/install/__init__.py`, `src/kg/install/manifest.py`, `tests/install/__init__.py`, `tests/install/test_manifest.py`

**Interfaces — Produces:** `Manifest(home: Path)` with `record(harness, paths: list[Path])`, `get(harness) -> list[str]`, `clear(harness)`. Stored at `<home>/.kg-install/<harness>.json`.

- [ ] **Step 1: Failing test**

```python
# tests/install/test_manifest.py
from pathlib import Path
from kg.install.manifest import Manifest


def test_record_get_clear(tmp_path):
    m = Manifest(tmp_path)
    m.record("claude-code", [tmp_path / "a.json", tmp_path / "b.md"])
    assert str(tmp_path / "a.json") in m.get("claude-code")
    m.clear("claude-code")
    assert m.get("claude-code") == []
```

- [ ] **Step 2: Run FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/install/manifest.py
from __future__ import annotations
import json
from pathlib import Path


class Manifest:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.dir = self.home / ".kg-install"

    def _path(self, harness: str) -> Path:
        return self.dir / f"{harness}.json"

    def record(self, harness: str, paths: list[Path]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        existing = set(self.get(harness))
        existing.update(str(p) for p in paths)
        self._path(harness).write_text(json.dumps(sorted(existing)), encoding="utf-8")

    def get(self, harness: str) -> list[str]:
        p = self._path(harness)
        return json.loads(p.read_text()) if p.exists() else []

    def clear(self, harness: str) -> None:
        p = self._path(harness)
        if p.exists():
            p.unlink()
```

```python
# src/kg/install/__init__.py
from kg.install.manifest import Manifest
__all__ = ["Manifest"]
```
```python
# tests/install/__init__.py  (empty)
```

- [ ] **Step 4: Run PASS**
- [ ] **Step 5: Commit** — `feat(m3): install manifest (record/get/clear)`

---

### Task 3: Claude Code installer

**Files:** Create `src/kg/install/claude_code.py`, `tests/install/test_claude_code.py`

**Interfaces — Produces:** `install_claude_code(home: Path, project: Path, skills_src: Path, uninstall: bool) -> list[Path]`. Writes: `<home>/.claude/skills/kg-*` (copy from `skills_src`), `<home>/.claude.json` MCP entry `{command: kg, args: [mcp, serve]}`, `<home>/.claude/settings.json` SessionEnd hook calling `kg raw add --type conversation`, and a `CLAUDE.md` stanza appended to `<project>/CLAUDE.md`. Idempotent (merge MCP dict, merge settings hooks). Returns written paths; caller records manifest.

> Assumption: Claude Code's config lives at `~/.claude.json` (MCP servers) and `~/.claude/settings.json` (hooks). Verify against the running version at execution time; adjust paths if the harness moved them.

- [ ] **Step 1: Failing test**

```python
# tests/install/test_claude_code.py
import json
from pathlib import Path
from kg.install.claude_code import install_claude_code


def test_install_writes_mcp_and_skills(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    project = tmp_path / "proj"; project.mkdir()
    skills = tmp_path / "skills"; (skills / "kg-extract").mkdir(parents=True)
    (skills / "kg-extract" / "SKILL.md").write_text("extract")
    written = install_claude_code(home, project, skills, uninstall=False)
    # MCP entry present
    cfg = json.loads((home / ".claude.json").read_text())
    assert "kg" in cfg.get("mcpServers", {})
    # skills copied
    assert (home / ".claude" / "skills" / "kg-extract" / "SKILL.md").exists()
    # CLAUDE.md stanza
    assert "kg" in (project / "CLAUDE.md").read_text().lower()


def test_uninstall_removes_recorded(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    project = tmp_path / "proj"; project.mkdir()
    skills = tmp_path / "skills"; (skills / "kg-extract").mkdir(parents=True)
    (skills / "kg-extract" / "SKILL.md").write_text("x")
    install_claude_code(home, project, skills, uninstall=False)
    install_claude_code(home, project, skills, uninstall=True)
    assert not (home / ".claude" / "skills" / "kg-extract").exists()
```

- [ ] **Step 2: Run FAIL**
- [ ] **Step 3: Implement** — copy skills via `shutil.copytree(..., dirs_exist_ok=True)`; read/merge `.claude.json` JSON (set `mcpServers["kg"]`); read/merge `settings.json` adding a SessionEnd hook entry; append a delimited stanza to `CLAUDE.md` (idempotent: replace between markers). On uninstall, remove `mcpServers["kg"]`, remove copied skill dirs, remove hook, strip stanza. Return list of touched paths.
- [ ] **Step 4: Run PASS**
- [ ] **Step 5: Commit** — `feat(m3): kg install claude-code (skills + MCP + hook + stanza)`

---

### Task 4: Codex / OpenCode / Cursor / AGENTS.md installers

**Files:** Create `src/kg/install/codex.py`, `opencode.py`, `cursor.py`, `agents_md.py`; tests for each
**Interfaces — Produces:** each `install_<harness>(home, project, skills_src, uninstall) -> list[Path]` with the same contract. Codex: `~/.codex/prompts/` + `~/.codex/config.toml` mcp. OpenCode: `~/.opencode/` instruction + skills. Cursor: `<project>/.cursor/rules/kg.mdc` + `~/.cursor/mcp.json`. `agents_md`: write/refresh `<project>/AGENTS.md` inlined contract.

- [ ] **Step 1–5:** one TDD cycle per harness (failing test → impl → pass → commit). Each test asserts the target config file gains the kg entry and uninstall reverses it. Commit message: `feat(m3): kg install <harness>`.

> Note: keep each writer small and symmetric — they share the "render a config fragment + merge + record" shape. If a harness's real config location differs at execution time, fix the path constant in that one file.

---

### Task 5: `kg install` CLI dispatch

**Files:** Create `src/kg/cli/install_cli.py`, `tests/cli/test_install_cli.py`; Modify `src/kg/cli/main.py`

**Interfaces — Produces:** `install_cli(harness: str = "claude-code", uninstall: bool = False, all: bool = False)`. Dispatches to the right writer, records/clears manifest, prints written paths.

- [ ] **Step 1: Failing test** — invoke `kg install claude-code` in a tmp home (monkeypatch `Path.home()`), assert MCP entry written and stdout lists paths.
- [ ] **Step 2: Run FAIL**
- [ ] **Step 3: Implement** — dispatch table `{claude-code, codex, opencode, cursor, agents-md}`; `--all` loops. Use `Path.home()` (monkeypatchable).
- [ ] **Step 4: Run PASS**
- [ ] **Step 5: Commit** — `feat(m3): kg install CLI dispatch + --uninstall/--all`

---

### Task 6: FastMCP server — tools + resources

**Files:** Create `src/kg/mcp/__init__.py`, `src/kg/mcp/server.py`, `tests/mcp/__init__.py`, `tests/mcp/test_server.py`
**Interfaces — Produces:** `build_server(project_dir: Path) -> FastMCP` registering the 10 tools + 2 resources. Each tool locates `.kg/` in `project_dir`, builds the adapter/gate, calls core, returns JSON. `kg mcp serve` runs `build_server(Path.cwd()).run()`.

- [ ] **Step 1: Add dep** — `fastmcp>=2` (or `mcp>=1`), `uv sync`.
- [ ] **Step 2: Failing test**

```python
# tests/mcp/test_server.py
import json
from kg.mcp.server import build_server
from kg.cli.init import init_project


def test_save_pole_and_query(tmp_path):
    init_project(tmp_path, user_id="u", scope="s")
    s = build_server(tmp_path)
    tools = {t.name for t in s.list_tools_sync()} if hasattr(s, "list_tools_sync") else set()
    # fallback: call tool funcs directly if the SDK in-process API differs
    res = s.call_tool("save_pole", {"nodes": [{"type": "person", "name": "Demis"}],
                                     "edges": [], "source": "raw/x.md#chunk-0"})
    assert res  # non-empty
```

> The exact in-process test API depends on the installed FastMCP/MCP version. If `call_tool` isn't available, test the underlying registered handler functions directly (factor them into `src/kg/mcp/handlers.py` with plain signatures, and have `server.py` register them). Prefer the factored-handlers approach — it's testable without SDK gymnastics.

- [ ] **Step 3: Implement** — factor handlers into `src/kg/mcp/handlers.py` (`save_pole(project_dir, nodes, edges, source) -> dict`, etc.), each a thin call to core; `server.py` wraps them with FastMCP tool/resource decorators. Resources: `read_wiki_index(project_dir) -> str`, `read_ontology(project_dir) -> str`.
- [ ] **Step 4: Run PASS**
- [ ] **Step 5: Commit** — `feat(m3): FastMCP server — agent-shaped tools + resources`

---

### Task 7: `kg mcp serve` + `kg install` registration + SessionEnd hook script

**Files:** Create `src/kg/cli/mcp_cli.py`, `src/kg/hooks/session_end.py`; Modify `main.py`; Test `tests/cli/test_mcp_cli.py`

**Interfaces — Produces:** `mcp_serve_cli()` → `build_server(Path.cwd()).run()`. `session_end.py` is the hook entrypoint: reads the session transcript (from stdin/env per Claude Code hook protocol), formats via `conversation.format_conversation`, calls `raw_ops.add_source(..., conversation=True)`, logs to `wiki/log.md`.

- [ ] **Step 1–5:** TDD cycle. The hook test feeds a fixed transcript JSON on stdin and asserts a `raw/conversations/*.md` appears.
- [ ] **Step 6: Commit** — `feat(m3): kg mcp serve + SessionEnd conversation hook`

---

### Task 8: M3 acceptance — install → MCP round-trip

**Files:** `tests/test_acceptance_m3.py`

- [ ] **Step 1: Acceptance test** — in a tmp home + project: `kg install claude-code`; assert skills + MCP + hook written; call `save_pole` + `query_memory` handlers in-process; assert node persisted and query returns it; `kg install --uninstall claude-code` reverses.
- [ ] **Step 2: Full suite green**
- [ ] **Step 3: Commit** — `feat(m3): acceptance test; M3 complete`

---

## Self-Review

**Spec coverage (§6, §10, §11 M3 subset):** MCP agent-shaped tools + Resources (§6.2) → Task 6. `kg install <harness>` for 4 harnesses + AGENTS.md (§10) → Tasks 3,4,5. Conversation ingest hook (§10 continual-learning) → Task 7. `kg mcp serve` (§11) → Task 7. ✓ Gap: deep_search_memory is a thin wrapper over M4 deep-search — register the tool name now, defer impl to M4 (handler raises NotImplementedError until then, documented).

**Placeholder scan:** Task 4 (four harnesses) uses a repeated TDD-cycle description rather than re-pasting identical code for each — this is intentional symmetry, not a placeholder; each gets its own commit. Path-location assumptions are flagged inline.

**Type consistency:** `install_<harness>(home, project, skills_src, uninstall) -> list[Path]` uniform across Tasks 3,4; `Manifest` (Task 2) consumed by all writers; `build_server(project_dir)` (Task 6) matches `mcp_serve_cli`. ✓

---

## Execution Handoff

**Plan saved to `docs/superpowers/plans/2026-07-26-kg-m3-mcp-install.md`.** Next: **M4 (viz + Cypher subset + deep search)**, then M5–M6.
