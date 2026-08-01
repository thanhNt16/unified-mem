# E2E Ingest + Claude Code Validation

**Date:** 2026-08-01
**Runner:** isolated temp project `/tmp/kg-e2e.mFejZP`
**Command environment:** `uv run kg` (bare `PYTHONPATH=. kg` lacks installed Tree-sitter dependencies)

## PASS

| Flow | Evidence |
|---|---|
| Project init | `kg init` created `.kg/` successfully |
| Plain text / stdin | `kg raw add - --type text` stored raw markdown + registry entry |
| Markdown | `kg raw add <file>.md` preserved title/content/type |
| PDF | `kg raw add <file>.pdf` converted expected text into raw mirror |
| Local URL | Local HTTP HTML page ingested through `http://127.0.0.1:<port>/...` and converted content verified |
| Incremental index | First `kg index scan --mode fast` indexed; second scan skipped unchanged source |
| Claude Code install | Isolated `--project-root` / `--home-root` install generated MCP config, hooks, skills, manifest without touching `~/.claude` |
| Automated suite | 786 passed, 3 skipped before E2E findings |

## FAIL

### F1 — DOCX conversion accepts empty content

**Reproduction:** `kg raw add <doc>.docx` succeeds, but generated raw mirror content/SHA is empty.

**Expected:** Converted DOCX body persisted or command fails with conversion error.

**Severity:** High — silent data loss.

### F2 — staged rebuild CLI passes unsupported argument

**Reproduction:** `kg index rebuild --full`

**Actual:** `RebuildCandidate.create() got an unexpected keyword argument 'full'`.

**Expected:** Full rebuild candidate creates and publishes validated staging DB.

**Severity:** Medium — advertised command broken.

### F3 — query FTS syntax error for extracted content

**Reproduction:** `kg query <fixture query>` after E2E graph setup.

**Actual:** `sqlite3.OperationalError: no such column: FIXTURE`.

**Expected:** Query terms are safely translated for FTS5 or zero results returned; never raw SQLite error.

**Severity:** High — user query crashes.

## Notes

- Bare `PYTHONPATH=. kg` lacks runtime Tree-sitter packages after new code-index dependency addition. Use `uv run kg`; packaging/install smoke should be added separately.
- DOCX is a required conversion path; add a non-empty DOCX fixture regression test.
- Rerun this E2E matrix after fixes, including isolated Claude Code install.
