# Harness setup

`kg` is harness-agnostic. `kg install <harness>` writes the skills, hooks, and MCP
config so a harness's LLM can drive the same `.kg/` memory as any other. The same
memory answers the same question on Claude Code *and* Codex — the portability claim,
proven end-to-end (M5 acceptance).

## Realized harness names

`kg install` takes one of:

| Harness arg | Target | Skills delivery | Transport |
|---|---|---|---|
| `claude` | Claude Code | plugin: 4 skills + `kg` in PATH | shell (CLI) primary; MCP optional |
| `codex` | Codex CLI | plugin/prompts port of same `SKILL.md` | shell primary; MCP |
| `opencode` | OpenCode | instruction file + skills dir | shell; MCP |
| `cursor` | Cursor | rules file summarizing the skill contract | MCP primary (agent mode) |
| `agents` | Anything else | generated `AGENTS.md` (inlined contract) | MCP stdio |
| `all` | All of the above | Plan-only (preflight every plan, write nothing) | n/a |

> **Note:** the harness token is `claude`, not `claude-code`.

## Plan first, apply second

`kg install` is **dry-run by default** — it prints a deterministic plan (paths,
fragments, manual steps, conflicts) and writes nothing. Apply with `--apply`:

```bash
kg install claude                       # plan only — review what would change
kg install claude --apply               # write owned fragments + manifest
```

`--apply` is the sole write boundary. Every written fragment is fingerprinted and
recorded in `.kg-install-manifest.json` so uninstall is exact and reversible.

## Targeting a non-default project or home

```bash
kg install claude --apply \
  --project-root /path/to/project \
  --home-root $HOME
```

Defaults are cwd and `$HOME`. `--project-root` must contain a `.kg/` directory.

## Skills source override

```bash
kg install claude --apply --skills-src /abs/path/to/skills
```

Default skills source resolution: `<project>/skills` if present, else the
bundled skills shipped inside the `kg` package. The bundled copy makes
`kg install` work on any project without checking out the skills repo. Use
`--skills-src` only when shipping a forked copy alongside a custom harness.

## MCP server

`kg mcp serve` runs the kg MCP server over stdio. stdout emits JSON-RPC only.

```bash
kg mcp serve --project-root /path/to/project            # read-only
kg mcp serve --project-root /path/to/project --allow-writes
```

`--allow-writes` enables write tools (`save_pole`, `review_same_as`); default is
read-only. Claude Code's installer wires the MCP server into the plugin config;
other harnesses document the stdio invocation in their rules/instruction file.

## Session-end hook (Claude Code)

```bash
kg hook session-end --project-root /path/to/project \
  [--session-root /path/to/project/sessions]
```

Reads a SessionEnd payload on stdin, ingests the conversation transcript into
`.kg/raw/conversations/`, appends to `registry.jsonl`. `--session-root` is a
trusted directory under `--project-root` containing transcript files referenced
by `payload.transcript_path`. The installer wires the hook into Claude Code's
settings; other harnesses use a session-end wrapper script (manual step in the
install plan).

## Conflict override (Claude only)

```bash
kg install claude --apply --force
```

`--force` is forwarded only to harnesses that support conflict override (claude).
It never bypasses drift checks at uninstall.

## Uninstall

```bash
kg install claude --uninstall
```

Loads the committed manifest and removes the exact owned fragments. Drift (a
fragment changed after install) raises an actionable error and exits nonzero —
the engine never silently overwrites user edits.

## All harnesses (plan only)

```bash
kg install all
```

Preflight every plan before any apply. Because installers use one manifest per
project, applying all would overwrite ownership — `--apply` with `all` is
rejected. Apply each harness individually.

## What to read next

- [Quickstart](quickstart.md) — `kg init`, first save, first search.
- [Architecture overview](../architecture/overview.md) — why one memory serves many harnesses.
- [Troubleshooting](../ops/troubleshooting.md) — drift errors, MCP won't start, missing skills.
