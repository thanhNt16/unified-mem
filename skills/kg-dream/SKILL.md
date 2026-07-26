---
name: kg-dream
description: Engine-proposes / harness-judges review queue. Surfaces gray-zone same_as pairs and other candidates (recent-pair, expiring, orphan, contradict) via `kg dream candidates`, then executes the human/model verdict with `kg merge` / `kg review confirm` / `kg review reject`.
---

# kg-dream

Dream = the graph surfaces what it could not auto-resolve; the harness decides. The engine never writes a verdict without an explicit confirm/reject.

> **M2 status:** `kg snapshot`, `kg wiki sync` are implemented. `kg wiki build` (deep-search wiki) is deferred to M4.

## When to run

- The user says "review the graph", "what needs attention", "clean up duplicates".
- After a bulk extract run (`/kg:extract`) — gray-zone pairs land here.
- Periodically as graph hygiene. The engine advances NO marker on its own; the user/harness decides when the queue is "done".

## Non-negotiable: no implicit state

`kg dream candidates` is **read-only**. It writes nothing — no marker file, no DB mutation, no `last_dream.txt`. Re-running it is always safe and always returns the current candidate set.

`--since` is **explicit and optional**:
- Omitted → returns candidates derived from **all active nodes** (T1 semantics).
- `--since 7d` / `--since 12h` / `--since 30m` → restricts the RECENT-PAIR sweep to nodes whose `created_at`/`updated_at` ≥ cutoff.
- `--since 2026-07-20T00:00:00Z` → ISO-8601 timestamp cutoff.

The review is complete when the harness says so, not when the queue empties. Truncation does not advance any cursor.

## The loop

1. **Inspect the queue**:
   ```
   kg dream candidates
   ```
   Optional filters:
   ```
   kg dream candidates --since 7d
   kg dream candidates --kind pending --json
   ```
   Valid kinds (case-insensitive): `recent-pair`, `pending`, `expiring`, `orphan`, `contradict`.

   Output (human mode) is one line per candidate, deterministic order (reason → score desc → node ids):
   ```
   pending  score=0.89  edge=u:person:a|same_as|u:person:b  nodes=u:person:a u:person:b  gray-zone same_as
   recent-pair  score=0.93  nodes=u:person:x u:person:y  person pair (canonical 'X' vs 'Y')
   orphan  score=0.00  nodes=u:fact:z  orphan
   ```

   `--json` emits a deterministic array via `dataclasses.asdict` — feed it to scripts, not to humans.

2. **For each candidate, decide one of:**

   | Verdict | When | Command |
   |---|---|---|
   | **confirm** | `pending` same_as: the two endpoints ARE the same entity. | `kg review confirm <EDGE_ID> --winner <NODE_ID> [--reason ...]` |
   | **reject** | `pending` same_as: the two endpoints are DISTINCT. | `kg review reject <EDGE_ID> [--reason ...]` |
   | **merge** | `recent-pair` / `contradict`: clear duplicate, no pending edge to confirm. | `kg merge <WINNER_ID> <LOSER_ID> [--reason ...]` |
   | **keep** | Not enough signal. Leave the graph alone. | (no command) |
   | **escalate** | Needs the user. Surface it and stop. | (no command) |

   See `references/review-protocol.md` for the full decision tree per candidate kind.

3. **Authoritative rules (non-negotiable)**:

   - **Confirm/reject are for `pending` edges only.** The CLI validates `winner` is one of the edge endpoints, blocks self-merge, blocks type-mismatch, and runs all mutations in one transaction (winner enriched → edges repointed → loser tombstoned → pending edge deleted). On failure it rolls back.
   - **Merge is explicit/manual.** `WINNER_ID` is the first argument and IS the survivor. `kg merge a b` → `a` survives, `b` is tombstoned. The engine never treats an arbitrary active `same_as` as merge authority — only the explicit CLI call.
   - **Canonical = `status=="active"`.** `canonical_name` is presentation only, not merge authority. Use the node `id` (which encodes type + name) as the survivor signal; `canonical_name` may be edited freely.
   - **Auto-merge only inside confirm/reject.** Recent-pair candidates are NOT auto-merged; they require explicit `kg merge`.
   - **Source/type/active guards are server-side.** The harness cannot talk its way past them.

4. **After each verdict**, the engine prints the audit line. Surface it to the user verbatim:
   ```
   confirmed u:person:a|same_as|u:person:b; merged u:person:b into u:person:a
   rejected u:person:a|same_as|u:person:b
   merged u:person:y into u:person:x
   ```

5. **After the batch** (only if at least one merge/confirm changed the graph):
   ```
   kg wiki sync
   kg snapshot
   ```
   - `kg wiki sync` regenerates affected entity pages (`wiki/entities/<slug>.md`) and appends a structured JSONL line per merge/confirm/reject to `wiki/log.md`.
   - `kg snapshot` writes a WAL-safe zstd artifact under `.kg/snapshots/` for recoverability.

   Skip both if every verdict was `keep` / `escalate`.

## Lineage and audit

Every `kg merge`, `kg review confirm`, and `kg review reject` writes a structured JSONL line to `wiki/log.md`:

```json
{"timestamp": "...", "action": "review_confirm", "winner_id": "...", "loser_id": "...", "review_edge_id": "...", "winner_before": {...}, "loser_before": {...}, "edges_before": [...], "reason": "..."}
```

The DB commit happens BEFORE the log append. If the log append fails (e.g. disk full), the engine warns but the DB change stays committed — the audit is best-effort, the merge is authoritative. The user should know: data integrity first, perfect audit second.

## What this skill does NOT do

- **Advance any marker on truncation.** No `last_dream.txt`. No implicit "I reviewed this" cursor. Re-run freely.
- **Auto-merge.** Every merge is explicit. The dreamer proposes; the harness disposes.
- **Touch raw.** Dream reads the graph only. Raw stays cold.
- **Guess.** If a candidate is ambiguous, escalate to the user. Do not fabricate a verdict.

## Failure modes

- **No `.kg/` in cwd** → `kg dream candidates` errors with "Run `kg init` first." Surface verbatim.
- **Empty queue** → `kg dream candidates` prints `no candidates`. Not an error.
- **`--since` unparseable** → engine treats invalid durations/timestamps as `None` (all active nodes). If you need strict cutoff, validate the value before passing it.
- **Confirm on already-consumed edge** → engine errors with "edge <id> not pending". Move on; the candidate list is stale, re-run it.
- **Type mismatch on merge** → engine errors with "type mismatch". Pick the right endpoints or escalate.

## References

- `references/review-protocol.md` — full per-kind decision tree with worked examples.
- Spec §8 (dream / review) in `docs/superpowers/specs/2026-07-26-kg-unified-memory-design.md`.
- Realized APIs: `src/kg/dream.py`, `src/kg/cli/dream.py`, `src/kg/cli/review.py`, `src/kg/gate.py`.
