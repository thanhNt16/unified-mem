---
name: kg-ingest
description: Land a raw source (file, URL, or stdin) into `.kg/raw/` with chunk boundaries, ready for `/kg-extract`. Thin wrapper over `kg raw add` / `kg raw list`. Run this first — extraction assumes a source is already ingested.
---

# kg-ingest

Ingest is the entry point of the kg pipeline: it converts an external source
into normalized markdown under `.kg/raw/` with `chunks:` frontmatter. Nothing
else (`/kg-extract`, `/kg-query`, `/kg-dream`) works until a source is ingested.

The harness does **not** write to `.kg/raw/` directly. `kg raw add` is the
only ingest path — it handles type detection, markitdown conversion, chunking,
and dedup (content-hash skip).

## When to run

- User says "ingest this", "add this doc", "remember this file/URL".
- Before `/kg-extract` — extraction reads from `.kg/raw/`.
- A transcript, PDF, docx, HTML page, or pasted text needs to enter the graph.

## Commands

### Add a source

```bash
kg raw add <source>           # path, URL, or '-' for stdin
```

Options:

| Flag | Value | Notes |
|---|---|---|
| `--type` | `pdf\|docx\|md\|html\|url\|text` | Optional; auto-detected if omitted |
| `--title` | string | Override the derived title |
| `--conversation` | flag | Mark as a chat transcript (SessionEnd hook uses this) |

Examples:

```bash
kg raw add ./README.md                         # local markdown
kg raw add ./paper.pdf                         # PDF → markitdown → chunks
kg raw add https://example.com/spec.html       # URL fetched via trafilatura
kg raw add ./transcript.json --conversation    # chat transcript
echo "paste text" | kg raw add -               # stdin → text source
```

Output on success: `added: raw/<hash>.md` → `next: run /kg-extract`.
Duplicate content (same content-hash) is skipped: `skipped (duplicate): …`.

### List sources

```bash
kg raw list                   # all sources
kg raw list --unextracted     # only sources not yet extracted
```

Tab-separated: `path<TAB>type<TAB>title[ [unextracted]]`.

## Workflow

1. **Ingest** — `kg raw add <source>` (this skill).
2. **Extract** — `/kg-extract` pulls POLE entities + Object/Fact/Preference into the graph via `kg save`.
3. **Query** — `/kg-query` for hybrid / NL-Cypher / deep-search.
4. **Dream** — `/kg-dream` surfaces gray-zone merge candidates for review.

## Notes

- `.kg/raw/` is content-addressed (`<hash>.md`); the same source ingested twice
  is a no-op.
- Chunk boundaries live in frontmatter (`chunks:`). If absent, the file was
  written by an older path — re-add it.
- Ingest writes only to `.kg/raw/`. The graph (`kg save`) is a separate step
  owned by `/kg-extract`.
