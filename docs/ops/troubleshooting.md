# Troubleshooting

Common issues and fixes.

## kg: command not found

`make install` installs kg via `uv tool install`. Verify:

```bash
which kg          # should point to ~/.local/bin/kg or similar
kg --version     # should print kg <version>
```

If missing, run `make install` (or `make dev` for editable).

## kg init fails: .kg/ already exists

`kg init` refuses to overwrite an existing `.kg/`. To replace it:

```bash
rm -rf .kg
kg init --user-id $USER --scope my-project
```

To restore from a committed snapshot instead:

```bash
rm -rf .kg
kg init --user-id $USER --scope my-project --from-snapshot snapshots/kg.db.zst --force
```

## kg install: drift error on uninstall

`kg install <harness> --uninstall` detects fragments that changed after install.
This prevents silently overwriting user edits.

```
error [claude]: drift detected in .claude/settings.json (fingerprint mismatch)
```

**Fix:** manually reconcile the changed fragment, or remove the file and uninstall:

```bash
kg install <harness> --uninstall
```

## kg install: unknown harness

```
error: unknown harness 'foo'. One of: claude, codex, opencode, cursor, agents, all.
```

Use one of the realized harness names. The token is `claude`, not `claude-code`.

## kg install all --apply rejected

```
error [all]: installers use one .kg-install-manifest.json per project;
applying all would overwrite ownership. Apply an individual harness.
```

Apply each harness individually: `kg install claude --apply`, then `kg install codex --apply`, etc.

## kg mcp serve: project root not found

```bash
kg mcp serve --project-root /path/that/contains/.kg
```

The path must contain a `.kg/` directory. `kg mcp serve` does not create one.

## kg save: ontology validation error

```
error: unknown type 'foo' — not in ontology.json
```

The extractor emitted a node type not in `ontology.json`. Fix the extraction
prompt or add the type to the ontology (project-level, versioned edit).

## kg wiki sync: no output

`kg wiki sync` prints counts. If it prints `synced 0 pages, removed 0 stale,
preserved 0 user files`, the graph has no active nodes — extract and save first.

## kg search: no results

1. Verify the graph has nodes: `kg status`.
2. Try a simpler query: `kg search "Paris"`.
3. Try keyword mode: `kg search "Paris" --mode bm25`.
4. If using a custom embedder, verify it loaded: `kg config get embedding.provider`.

## kg snapshot: file exists

`kg snapshot` writes to `.kg/snapshots/kg.db.zst` by default. To overwrite:

```bash
rm .kg/snapshots/kg.db.zst
kg snapshot
```

Or use `-o` for a custom path.

## Embedding model download on first search/save

The default local embedder (`bge-small-en-v1.5` via FastEmbed/ONNX) downloads
the model on first use (~90 MB). This is cached by FastEmbed and only happens
once per machine. If the download fails, check network access or set a proxy.

## Deferred features (not bugs)

- `kg cypher` rejects WRITE clauses (CREATE, MERGE, SET, DELETE, CALL) — by design. Read-only Cypher is the only realized mode.
- `kg wiki sync --touched` does not exist. Use `kg wiki sync` (regenerates all active nodes).
- `kg export --cypher` is not implemented.
- `kg install claude-code` is a typo — use `kg install claude`.

## What to read next

- [Runbook](runbook.md) — snapshot, dream, wiki sync workflows.
- [Harness setup](../guides/harness-setup.md) — install and uninstall details.
