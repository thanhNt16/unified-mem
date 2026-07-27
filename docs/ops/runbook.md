# Runbook

Day-to-day operations for `.kg/` memory. Assumes kg is installed and `kg init`
has run (see [quickstart](../guides/quickstart.md)).

## Snapshot and restore

`kg.db` is derived and gitignored. `raw/`, `registry.jsonl`, `wiki/`, and a
compressed snapshot of the DB are the committed artifacts. A teammate boots
warm from the snapshot.

### Snapshot

```bash
kg snapshot                          # writes .kg/snapshots/kg.db.zst
kg snapshot -o /tmp/backup.db.zst    # custom path
```

Then commit the snapshot with `raw/` and `registry.jsonl`:

```bash
git add .kg/raw .kg/wiki .kg/registry.jsonl .kg/snapshots/kg.db.zst
git commit -m "memory: snapshot $(date +%F)"
```

### Restore

```bash
kg init --user-id $USER --scope my-project \
  --from-snapshot snapshots/kg.db.zst
```

To replace an existing `.kg/kg.db` on restore, add `--force`. Vectors re-embed
lazily if the embedder differs (vector dimension or `sha256(model)` mismatch in
config).

## Dream and review

`kg dream` surfaces consolidation candidates — engine proposes, harness judges,
engine executes. Nothing above the engine threshold (0.95) auto-merges.

### Worklist

```bash
kg dream candidates                  # all kinds, human-readable
kg dream candidates --since 7d       # only recent pairs
kg dream candidates --kind PENDING   # filter by kind
kg dream candidates --json           # machine-readable
```

Kinds: `RECENT-PAIR`, `PENDING`, `EXPIRING`, `ORPHAN`, `CONTRADICT`, `WIKI-LINT`.
Each line carries a `score=`, the node IDs, and an optional edge ID.

### Merge

```bash
kg merge <winner-id> <loser-id> --reason "same person, CEO match"
```

The loser is tombstoned (`status: tombstoned`, `merged_into: <winner>`), its
edges re-pointed to the winner, both pre-images logged to `wiki/log.md`. Merge
is the one unrecoverable move — undo by re-extracting from `raw/`.

### Same_as review

```bash
kg review list                       # show pending same_as edges
kg review confirm <edge-id> --winner <node-id> --reason "confirmed same"
kg review reject <edge-id> --reason "distinct entities"
```

`review confirm` merges; `review reject` dismisses. Both write to
`wiki/log.md`.

### Close out

```bash
kg wiki sync && kg snapshot
```

Writes collision-safe entity pages, removes stale ones, preserves user files,
then snapshots the cleaned DB. Append counts per kind to `wiki/log.md`.

## Wiki sync

```bash
kg wiki sync
```

Writes entity pages for every active node in the graph, removes stale pages
(nodes no longer active), and preserves user-authored files (notes, deep-search
pages). Collision-safe: a page kg owns gets regenerated; a page a user wrote
gets skipped. No flags.

### Lint

```bash
kg wiki lint
```

Reports orphan pages, broken `[[wikilinks]]`, and stale summaries (page says X,
graph says Y). Wired into dream as the `WIKI-LINT` candidate kind.

### Deep search wiki

```bash
kg wiki build from-query "how does the chunking work" --hops 3
```

Materializes `wiki/deep/<slug>/` with an index, entity pages, and cross-links
for exploratory queries with 50+ hits. Cached by query slug + graph version;
stale on version bump.

## Status

```bash
kg status
```

Reports sources ingested, node/edge counts, pending reviews, and staleness.

## Common operations

### Re-extract after bad extraction

```bash
# Identify affected nodes by lineage, delete them, re-extract the raw file
# (via the extract skill calling kg save with the corrected chunk output).
```

The DB is rebuildable from `raw/`. Never edit `kg.db` by hand — go through the
extract skill or `kg save`.

### Reset and rebuild

```bash
rm .kg/kg.db
# re-run the extract skill over raw/
```

`registry.jsonl` retains per-chunk checkpoint state, so extraction resumes from
where it left off.

### Add a teammate's snapshot

```bash
git pull
kg init --user-id $USER --scope my-project --from-snapshot .kg/snapshots/kg.db.zst --force
```

## Deferred

- **Deep Cypher write operations** — not realized. `kg cypher` is read-only by construction.
- **`kg export --cypher`** — not yet implemented.
- **Auto-dream session hook** — config key present, runtime wiring deferred.

## What to read next

- [Troubleshooting](troubleshooting.md) — drift, MCP failures, missing skills.
- [Architecture overview](../architecture/overview.md) — why `.kg/` looks the way it does.
