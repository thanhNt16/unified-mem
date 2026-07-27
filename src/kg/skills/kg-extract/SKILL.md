---
name: kg-extract
description: Extract POLE entities (Person, Organization, Location, Event) plus Object/Fact/Preference from a raw source into the local knowledge graph. Triggered after `/kg-ingest` or on demand. The harness LLM does the extraction; `kg save` is the only write path.
---

# kg-extract

Turn raw markdown in `raw/` into graph nodes and edges. Extraction is **harness-side intelligence**: this skill prompts the model per chunk, validates JSON against the ontology contract, then hands the result to `kg save`, which runs the deterministic normalization gate (§6.3 of the spec). The model never writes to the graph directly.

> **Deferred to M2:** `kg raw rechunk` is not yet implemented in M1; the command will exit with an error.

This skill assumes `/kg-ingest` has already placed the source in `raw/` with chunk boundaries in frontmatter. If you don't see frontmatter `chunks:`, run `kg raw rechunk <file>` first.


## When to run

- After `/kg-ingest` lands a new source and you want it in the graph.
- On demand: the user points at `raw/<file>.md` and says "extract this".
- Re-run after editing extraction prompts or ontology — chunks are idempotent and safe to re-run; the gate deduplicates.

## The loop (spec §6.2)

For each raw file the user names:

   > **Deferred to M2:** `kg raw status` is not yet implemented in M1; the command will exit with an error.

1. **Read the registry entry** — `kg raw status <file>` returns the per-chunk state (`chunks_done`, `chunks_failed`). Resume from the first undone chunk; do not re-run completed chunks unless the user asks.

2. **For each chunk** (chunk boundaries come from frontmatter, 512 tok / 64 overlap, split on markdown headings then size — never mid-sentence):
   a. Load the chunk text from `raw/<file>.md` between the byte offsets in frontmatter.
   b. Build the extraction prompt (see `references/extraction-prompt.md`) using:
      - the chunk text only (NO IDs, NO prior graph state),
      - the ontology contract from `src/kg/ontology.py` (mirrored in `references/output-schema.json`),
      - the output schema (a list of node objects + a list of edge objects that reference node `name`s within this same record).
   c. Ask the harness LLM to emit one JSON object matching `references/output-schema.json`.
   d. **Validate** the JSON against `references/output-schema.json`:
      - Every `node.type` MUST be in `ALLOWED_NODE_TYPES`.
      - Every `edge.semantic_type` MUST be in `ALLOWED_SEMANTIC_EDGE_TYPES`.
      - Every edge `source_name`/`target_name` MUST match a node `name` in the same record.
      - On failure: append the validator error to the prompt and retry, max 2 retries. After that, mark the chunk `failed: {n: reason}` in the registry and continue — failures never halt the file.
   e. **Save** the validated record:
      ```
      kg save \
        --nodes <(jq '.nodes' extracted.json) \
        --edges <(jq '.edges' extracted.json) \
        --facts <(jq '.facts // []' extracted.json) \
        --preferences <(jq '.preferences // []' extracted.json) \
        --source raw/<file>.md#chunk-<n>
      ```
      The engine runs the gate (validate → resolve → embed → dedup → route) and prints per-entity decisions:
      ```
      NEW u:person:demis-hassabis
      RESOLVED-TO u:organization:deepmind
      MERGED-INTO u:object:alphafold (0.97)
      FLAGGED u:location:paris (0.89)  → review/same_as.md
      ```
      Echo these decisions to the user as they arrive.

      > **Deferred to M2:** `kg raw checkpoint` is not yet implemented in M1; the command will exit with an error.

   f. **Checkpoint** after each chunk: `kg raw checkpoint <file> --chunk <n> --status done|failed`.
3. **After the file** finishes:

   > **Deferred to M2:** `kg wiki sync` is not yet implemented in M1; the command will exit with an error.

   - Run `kg wiki sync --touched <file>` to regenerate affected entity pages (`wiki/entities/<slug>.md`).

   - Mark the registry entry `extracted`.
   - Surface the gray-zone list to the user: "N pairs flagged — run `/kg-dream` or `kg review list`."

## Extraction is name-space-local (non-negotiable)

Edges reference node `name`s **inside the same chunk record only**. The model never sees IDs, and never sees graph state. The engine resolves names → IDs *after* the gate settles both endpoints (see spec §6.2, §6.3). This makes chunks:

- **Embarrassingly parallel** — multiple chunks can be extracted concurrently.
- **Re-runnable** — a re-extract produces the same name-space record; the gate deduplicates against the existing graph.
- **Deterministic at the boundary** — model output is validated before it touches the engine; gate behavior is fully deterministic.

Do NOT violate this by feeding the model "existing node IDs" or "the current graph". It will hallucinate merges and the gate cannot recover.

## POLE + the rest

The ontology contract (`src/kg/ontology.py`) defines the allowed node and edge types; `references/output-schema.json` mirrors these exactly. POLE is the spine:

- **P**erson — `person`
- **O**rganization — `organization`
- **L**ocation — `location`
- **E**vent — `event`

Plus:

- `object` (with `subtype` such as `software`, `dataset`, `document`, `tool`) for things that aren't POLE
- `preference` (with `valid_from`/`valid_until`) — user tastes, tool choices
- `fact` — atomic factual claims (`subject`/`predicate`/`object`); facts are islands — they skip graph matching but get embedded + near-duplicate-checked
- `document`, `chunk`, `conversation`, `session` — structural nodes managed by other skills, rarely hand-extracted

When unsure between `fact` and `object`: a `fact` is an atomic claim that could be true or false ("AlphaFold 3 released in 2024"); an `object` is a thing ("AlphaFold 3"). Lean `object` for things that have names.

## Output shape

Each chunk produces exactly one JSON object of this shape (full schema in `references/output-schema.json`):

```json
{
  "nodes": [
    {
      "type": "person",
      "subtype": null,
      "name": "Demis Hassabis",
      "summary": "Co-founder and CEO of DeepMind.",
      "aliases": [],
      "attributes": { "role": "CEO" }
    }
  ],
  "edges": [
    {
      "source_name": "Demis Hassabis",
      "semantic_type": "employed_by",
      "target_name": "DeepMind",
      "summary": null,
      "confidence": 0.9
    }
  ],
  "facts": [
    { "subject": "AlphaFold 3", "predicate": "released_in", "object": "2024" }
  ],
  "preferences": []
}
```

Notes:
- Node `name` is the surface form from the source text — do NOT normalize casing. The gate normalizes.
- `aliases` only when the source uses a distinct surface form ("AI", "artificial intelligence").
- `attributes` are high-signal structured fields; bag the rest into `summary`.
- `facts` and `preferences` are separate arrays — they are stored as `fact`/`preference` nodes by the engine.

## Checkpointing & resumability

The unit of checkpoint is **the chunk**. The registry line for each raw file holds:

```
raw/demis.md  sha256=...  chunks_done=[0,1,2,4]  chunks_failed={3: "schema violation: unknown edge type 'works_at'"}
```

Resume reads this line and picks up at chunk 3 (retry) or chunk 5 (next undone). Never re-run done chunks silently — the user must opt in (`--force`).

## Concurrency

`kg save` takes a process-wide file lock on `kg.db` and queues writes (SQLite WAL, one writer). You may run multiple chunk extractions in parallel; they will serialize at save. Cross-process blind spots (two processes see the same gray-zone pair before either commits) are caught later by `/kg-dream`'s RECENT-PAIR job — do NOT try to handle them in extract.

## Failure modes

- **Schema violation** (unknown type / dangling edge endpoint) → retry with validator error (max 2), then checkpoint `failed` and continue.
- **`kg save` reports FLAGGED** → not a failure. Echo it, append to `review/same_as.md`, move on.
- **`kg save` reports MERGED-INTO** → not a failure. The model over-extracted a duplicate; the gate caught it.
- **Embedding model unavailable** → `kg save` falls back to the FakeEmbedder (configured locally); extraction still proceeds. Surface this once at the start of the run if `kg status` reports `embedder=fallback`.

## What this skill does NOT do

- **Merge.** Merges happen in the gate at ≥0.95 or via `/kg-dream`. Extract always emits names verbatim from the source.
- **Write the graph.** The only write path is `kg save`. Never construct node IDs, never edit `kg.db` directly.
- **Ingest.** Use `/kg-ingest` to get sources into `raw/`. This skill assumes that is done.
- **Query.** Use `/kg-query`.

## References

- `references/extraction-prompt.md` — the prompt the harness LLM sees per chunk.
- `references/output-schema.json` — the JSON schema the model must emit; mirrors the ontology contract in `src/kg/ontology.py` and the gate's `extracted_nodes`/`extracted_edges` shapes.
- Spec §6.2 (extract→save flow), §6.3 (gate), §13 (failure modes) in `docs/superpowers/specs/2026-07-26-kg-unified-memory-design.md`.
- Ontology contract: `src/kg/ontology.py` (`ALLOWED_NODE_TYPES`, `ALLOWED_SEMANTIC_EDGE_TYPES`, `STRUCTURAL_EDGE_TYPES`).
