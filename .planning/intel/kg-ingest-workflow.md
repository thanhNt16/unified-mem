# /kg:ingest Workflow Specification

## Overview

Ingest is the evidence-preservation front-end. It accepts raw sources, hashes them, registers them in the source registry, and routes to specialized extraction engines based on source type.

**Principle:** Ingest is NOT extraction. It's evidence capture with provenance.

## Command Signatures

```bash
# Main entry point
kg ingest <source> [--type <type>] [--title <title>] [--tags <tags>]

# Status and listing
kg ingest status [--source <path>]
kg ingest list [--type <type>] [--unextracted-only]

# Registry management
kg ingest forget <sha256_or_path>
kg ingest rehash <path>
```

## Input Formats

| Source Type | File Formats | Processing Path |
|---|---|---|
| **markdown** | `.md`, `.markdown` | Direct to `raw/`, chunk on headings |
| **pdf** | `.pdf` | markitdown → markdown → `raw/` |
| **docx** | `.docx` | markitdown → markdown → `raw/` |
| **pptx** | `.pptx` | markitdown → markdown → `raw/` |
| **html** | `.html`, `.htm` | lynx/links2 → markdown → `raw/` |
| **code** | Any source code | Special handling (see codebase-memory integration) |
| **transcript** | `.json` (Claude format) | Session-end hook → conversation graph |
| **binary** | `.bin`, generic | Fingerprint only, no text extraction |

## Processing Steps

### 1. Evidence Fingerprinting

```bash
sha256sum <source> → extract SHA256
```

- If `SHA256` already exists in registry at `.kg/registry/index.jsonl`: skip with status
- Create canonical source path: `.kg/raw/<type>/<sha256>.<ext>`

### 2. Metadata Capture

```yaml
# .kg/registry/index.jsonl entry
{
  "sha256": "abc123...",
  "path": ".kg/raw/markdown/abc123.md",
  "source": "/original/path/to/file.md",
  "type": "markdown",
  "title": "Architecture Decision Record #42",
  "ingested_at": "2026-08-01T00:00:00Z",
  "extracted": false,
  "chunks_done": [],
  "chunks_failed": {},
  "tags": ["adr", "architecture"]
}
```

### 3. Format Normalization

```bash
# Non-markdown sources → markdown
kg ingest --normalize <source>  # calls markitdown internally

# Output: .kg/raw/<type>/<sha256>.md
```

### 4. Chunking (markdown only)

```yaml
# Frontmatter added to .kg/raw/<type>/<sha256>.md
---
chunks:
  - start: 0
    end: 512
    heading: "Introduction"
  - start: 513
    end: 1024
    heading: "Background"
chunk_config:
  tokens: 512
  overlap: 64
  split_strategy: "heading_then_size"
---
```

Chunk boundaries are:
- Primary: Markdown headings (`##`, `###`)
- Secondary: Token count with 64-token overlap
- Never: Mid-sentence breaks

### 5. Registry Write

```bash
# Atomic append to .kg/registry/index.jsonl
kg ingest --register <metadata-file>
```

## Output Artifacts

| Artifact | Location | Purpose |
|---|---|---|
| **Raw source** | `.kg/raw/<type>/<sha256>.md` | Evidence preservation |
| **Chunk frontmatter** | Same file | Extraction boundaries |
| **Registry entry** | `.kg/registry/index.jsonl` | Provenance tracking |
| **Ingest report** | stdout (JSON) | Automation feedback |

### Ingest Report Schema

```json
{
  "sha256": "abc123...",
  "status": "registered" | "skipped" | "replaced",
  "path": ".kg/raw/markdown/abc123.md",
  "chunks": 12,
  "type": "markdown",
  "extracted": false,
  "next_action": "kg extract abc123"
}
```

## Integration Points

### codebase-memory-mcp Integration

When `--type code` or source path matches project code patterns:

```bash
kg ingest src/lib/auth.py --type code
```

Flow:
1. Register source in `.kg/registry/index.jsonl` (standard)
2. Write placeholder in `.kg/raw/code/<sha256>.md`:
   ```yaml
   ---
   source_type: code
   original_path: src/lib/auth.py
   language: python
   codebase_memory_project: "unified-mem"
   external_ref: "codebase-memory://function:authenticate_user"
   chunks: []  # Chunking delegated to codebase-memory
   ---
   ```
3. Route extraction to codebase-memory-mcp (see `/kg:extract` spec)

### Graphify Integration

When source is a conversation transcript:

```bash
kg ingest session-abc.json --type transcript
```

Flow:
1. Register with `type: transcript`
2. Parse conversation JSON → structured nodes:
   - `session` node (conversation metadata)
   - `conversation` nodes (per-turn content)
   - `mentions` edges (entities referenced)
3. No chunking (transcripts are pre-structured)
4. Set external reference for Graphify cross-check

### AgentMemory Integration

When `--source agent-memory`:

```bash
kg ingest --source agent-memory --since 7d
```

Flow:
1. Call `mcp__plugin_agentmemory_agentmemory__memory_recall(query="*", limit=1000)`
2. Convert returned observations to POLE + fact nodes
3. Register as batch under synthetic SHA256 (date hash)
4. Route extraction to AgentMemory format handler

### wiki Integration

After ingest completes:

```bash
kg ingest --wiki-sync <sha256>
```

- Create stub wiki page: `wiki/sources/<sha256>.md`
- Auto-link from existing entity pages that mention this source

## Specialized Engine Routing

```python
# Pseudo-code for routing logic
def route_extraction(source_type, source_path):
    if source_type == "code":
        return "codebase-memory-mcp"
    elif source_type == "transcript":
        return "graphify"
    elif source_path.startswith("agent-memory:"):
        return "agentmemory"
    else:
        return "default"  # /kg:extract skill
```

## Command Workflow Examples

### Example 1: Direct markdown ingest

```bash
$ kg ingest docs/specs/security-adr.md
registered abc123... → .kg/raw/markdown/abc123.md (12 chunks)
next: kg extract abc123
```

### Example 2: PDF with auto-normalization

```bash
$ kg ingest ~/Downloads/research-paper.pdf
normalized research-paper.pdf → markdown (markitdown)
registered def456... → .kg/raw/pdf/def456.md (24 chunks)
next: kg extract def456
```

### Example 3: Code source routing

```bash
$ kg ingest src/kg/gate.py --type code --title "Gate normalization logic"
registered ghi789... → .kg/raw/code/ghi789.md
external_ref: codebase-memory://function:normalize
next: kg extract ghi789  # routes to codebase-memory-mcp
```

### Example 4: Batch ingest from directory

```bash
$ kg ingest docs/ --batch
registered 8 sources (4 skipped, 4 new)
  ✗ abc123 (already exists)
  ✓ new-source-1 (def456...)
  ✓ new-source-2 (ghi789...)
  ...
next: kg extract --batch --unextracted-only
```

## Error Handling

| Error | Action | User Message |
|---|---|---|
| **Source not found** | Abort | `Error: <path> does not exist` |
| **Invalid format** | Skip, log error | `Warning: <file> format not supported, skipping` |
| **Registry write fail** | Abort, leave orphan raw file | `Error: registry write failed (disk full?)` |
| **Chunking error** | Fallback to fixed-size chunks | `Warning: heading chunking failed, using fixed-size` |
| **Duplicate SHA256** | Skip with status | `Already registered: <sha256> (use --force to replace)` |

## Deferred Features (M2+)

- `kg ingest --web <url>`: HTTP fetch + ingest
- `kg ingest --git <repo>`: Git history ingest per commit
- `kg ingest --git-lfs`: Large file handling
- `kg raw rechunk`: Re-chunk existing raw files with new config
- `kg raw status`: Show per-chunk extraction state
- `kg raw checkpoint`: Update chunk state (used by /kg:extract)

## Dependencies

- **markitdown**: For PDF/DOCX/PPTX → markdown conversion
- **Registry**: `.kg/registry/index.jsonl` for provenance
- **Paths module**: `src/kg/paths.py` for canonical paths
- **Hooks**: Session-end hook for transcript auto-ingest
