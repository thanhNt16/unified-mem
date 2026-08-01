# /kg:extract Workflow Specification

## Overview

Extract converts raw evidence in `raw/` into typed knowledge (POLE nodes + semantic edges) via the normalization gate. This is the **harness-side intelligence** step: the model extracts per-chunk, the gate validates/resolves/embeds/dedups, then writes to `kg.db` via `kg save`.

**Principle:** Extraction is name-space-local. Chunks are isolated. No graph state leaks into extraction.

## Command Signatures

```bash
# Per-source extraction
kg extract <sha256_or_path> [--force] [--parallelism <n>]

# Batch operations
kg extract --batch [--unextracted-only] [--limit <n>]
kg extract --all  # DANGEROUS: re-extract everything

# Retry and repair
kg extract --retry-failed <sha256_or_path>
kg extract --chunk <n> <sha256_or_path>  # single chunk
```

## Input Formats

### Input Source: Raw Files with Chunk Frontmatter

```yaml
# .kg/raw/markdown/abc123.md
---
chunks:
  - start: 0
    end: 512
    heading: "Introduction"
  - start: 513
    end: 1024
    heading: "Background"
---
# Raw markdown content here...
```

### Input: Code Files (codebase-memory-mcp route)

```yaml
# .kg/raw/code/ghi789.md
---
source_type: code
original_path: src/lib/auth.py
language: python
external_ref: "codebase-memory://function:authenticate_user"
chunks: []  # Chunking delegated
---
```

### Input: Transcripts (Graphify route)

```yaml
# .kg/raw/transcript/jkl012.md
---
source_type: transcript
original_format: json
conversation_id: "session-abc"
chunks: []  # No chunking, pre-structured
---
```

## Processing Steps

### 1. Registry State Check

```bash
kg raw status <sha256>
```

Output:
```json
{
  "sha256": "abc123...",
  "path": ".kg/raw/markdown/abc123.md",
  "extracted": false,
  "chunks_done": [0, 1, 2, 4],
  "chunks_failed": {3: "schema violation: unknown edge type"}
}
```

Resume logic:
- If `chunks_done` non-empty: resume from first undone index
- If `chunks_failed` non-empty: retry failed chunks first
- If `extracted == true`: abort unless `--force`

### 2. Per-Chunk Extraction Loop

For each chunk index `n` in `[0 .. total_chunks - 1]`:

#### 2a. Load Chunk Text

```python
# Read byte offsets from frontmatter
chunk_text = raw_file[chunks[n].start:chunks[n].end]
```

#### 2b. Build Extraction Prompt

```
System: {from references/extraction-prompt.md}
User: Chunk (source: raw/markdown/abc123.md#chunk-3):
       """
       {chunk_text}
       """
       Emit one JSON object per the schema.
```

Constraints:
- No graph state
- No IDs
- No prior context
- Only chunk text

#### 2c. Call Harness LLM

```python
response = claude.messages.create(
    model=cfg.extraction.model,
    messages=[system_msg, user_msg],
    response_format={"type": "json_object"}
)
json_blob = response.content[0].text
```

#### 2d. Validate JSON

```python
# Schema validation
errors = validate_json(json_blob, schema="references/output-schema.json")
for error in errors:
    if retry_count < 2:
        # Append error to prompt and retry
        user_msg += f"\nVALIDATOR ERROR: {error}. Re-emit with fix."
        retry_count += 1
        goto 2c
    else:
        # Checkpoint failed
        kg raw checkpoint abc123 --chunk 3 --status failed --reason "{error}"
        goto next_chunk
```

Validation rules:
- Every `node.type` in `ALLOWED_NODE_TYPES`
- Every `edge.semantic_type` in `ALLOWED_SEMANTIC_EDGE_TYPES`
- Every `edge.source_name`/`target_name` matches a node in same record
- No dangling edge references

#### 2e. Save to Graph

```bash
kg save \
  --nodes <(jq '.nodes' extracted.json) \
  --edges <(jq '.edges' extracted.json) \
  --facts <(jq '.facts // []' extracted.json) \
  --preferences <(jq '.preferences // []' extracted.json) \
  --source raw/markdown/abc123.md#chunk-3
```

Gate output (echo to user):
```
NEW u:person:demis-hassabis
RESOLVED-TO u:organization:deepmind
MERGED-INTO u:object:alphafold (0.97)
FLAGGED u:location:paris (0.89) → review/same_as.md
```

Decision types:
- `NEW`: No duplicate found
- `RESOLVED-TO`: Exact name match (casing normalized)
- `MERGED-INTO`: High-confidence duplicate (≥0.95)
- `FLAGGED`: Gray-zone (0.85-0.95) → review queue

#### 2f. Checkpoint Chunk

```bash
kg raw checkpoint abc123 --chunk 3 --status done
```

Registry entry updated:
```json
{
  "chunks_done": [0, 1, 2, 3, 4],
  "chunks_failed": {}
}
```

### 3. Post-Extract Sync

After all chunks complete:

```bash
kg wiki sync --touched abc123
kg snapshot
```

- `kg wiki sync`: Regenerate `wiki/entities/<slug>.md` for affected entities
- `kg snapshot`: Write WAL snapshot to `.kg/snapshots/<version>.kg.zst`

Mark registry entry:
```json
{
  "extracted": true,
  "extracted_at": "2026-08-01T00:00:00Z"
}
```

### 4. Gray-Zone Surface

Report to user:
```
Extraction complete: 12/12 chunks done
Gray-zone pairs flagged: 3
Run /kg:dream to review
```

## Output Artifacts

| Artifact | Location | Purpose |
|---|---|---|
| **Graph nodes** | `kg.db` (nodes table) | Canonical knowledge |
| **Graph edges** | `kg.db` (edges table) | Semantic relationships |
| **Embeddings** | `kg.db` (embeddings table) | Vector search |
| **Review queue** | `kg.db` (pending edges) | `/kg:dream` input |
| **Registry checkpoint** | `.kg/registry/index.jsonl` | Extraction state |
| **Entity wiki pages** | `wiki/entities/<slug>.md` | Human-readable output |
| **Snapshot** | `.kg/snapshots/<version>.kg.zst` | Recovery point |

## Integration Points

### codebase-memory-mcp Path

When `source_type == code`:

```python
# Instead of chunk loop, call MCP
functions = mcp__codebase_memory__search_graph(
    project=cfg.project.name,
    query="all functions in src/lib/auth.py",
    label="Function"
)

for func in functions:
    # Convert codebase-memory nodes to kg ontology
    kg_node = {
        "type": "object",
        "subtype": "software",
        "name": func["name"],
        "summary": func.get("docstring"),
        "attributes": {
            "language": func["language"],
            "complexity": func.get("cyclomatic")
        }
    }
    # Save via kg save
```

### Graphify Path

When `source_type == transcript`:

```python
# Graphify has already parsed conversation → nodes/edges
# Just validate and save
validated = validate_graphify_output(transcript_nodes, transcript_edges)
kg save --nodes validated.nodes --edges validated.edges --source transcript/jkl012
```

### AgentMemory Path

When `source` from agent-memory:

```python
# AgentMemory returns observations as text
for obs in agent_memory_observations:
    chunk_text = obs["content"]
    # Run standard extraction loop
    extract_chunk(chunk_text, source=f"agent-memory:{obs['id']}")
```

### wiki Integration

After each chunk save:
```bash
kg wiki sync --incremental  # Update affected entity pages only
```

After full source extract:
```bash
kg wiki build --from-source abc123  # Full rebuild for this source
```

## Data Flow Diagram

```
┌─────────────┐
│  Raw File   │  .kg/raw/markdown/abc123.md (with chunk frontmatter)
└──────┬──────┘
       │ 1. Read chunk boundaries
       ↓
┌─────────────────┐
│  Chunk Loop     │  For each chunk 0..N
└──────┬──────────┘
       │ 2. Extract chunk text
       ↓
┌─────────────────────┐
│  Harness LLM        │  Extract entities (no graph state)
└──────┬──────────────┘
       │ 3. JSON output
       ↓
┌─────────────────────┐
│  Validator          │  Check ontology compliance
└──────┬──────────────┘
       │ 4. Valid JSON
       ↓
┌─────────────────────┐
│  kg save            │  Gate: validate→resolve→embed→dedup→route
└──────┬──────────────┘
       │ 5. Graph writes
       ↓
┌─────────────────────┐
│  kg.db              │  nodes + edges + embeddings
└─────────────────────┘

Parallel:
┌─────────────────────┐
│  kg wiki sync       │  Update entity pages
└─────────────────────┘
```

## Staging vs Canonical

### Staging (per-chunk)
- Location: Memory only (passed to `kg save`)
- Lifetime: Single chunk save call
- Purpose: Isolation, parallelism

### Canonical (post-gate)
- Location: `kg.db`
- Lifetime: Persistent
- Purpose: Query, merge, review

No separate staging DB. Gate is the boundary.

## Concurrency Model

```bash
# Parallel chunk extraction (N = CPU cores)
kg extract abc123 --parallelism 8
```

- Each chunk: independent LLM call + save
- `kg save`: Process-wide SQLite lock, queues writes
- Cross-process blind spots: Caught by `/kg:dream` RECENT-PAIR

## Error Handling

| Error | Action | Recovery |
|---|---|---|
| **Schema violation** | Retry 2x with error appended | After 2 fails: checkpoint `failed`, continue |
| **`kg save` FLAGGED** | Not an error; append to review queue | Surface to user, continue |
| **`kg save` MERGED** | Not an error; echo decision | Surface to user, continue |
| **Embedding model down** | Fallback to FakeEmbedder | Surface warning at start |
| **Chunk load fail** | Checkpoint `failed` | Continue to next chunk |
| **Registry write fail** | Abort entire file | Leave partial state, user re-runs |

## Deferred Features (M2+)

- `kg raw rechunk`: Re-chunk with new config
- `kg extract --incremental`: Only new chunks since last run
- `kg extract --resolve-only`: Skip extraction, just re-run gate
- `kg extract --validate`: Dry-run, no writes

## Dependencies

- **Harness LLM**: Claude 3.5 Sonnet (or configured model)
- **kg save**: Gate normalization (src/kg/gate.py)
- **Registry**: Checkpoint state
- **wiki**: Entity page generation
- **Validation schemas**: references/output-schema.json
