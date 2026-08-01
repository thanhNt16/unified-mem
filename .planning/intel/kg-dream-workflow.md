# /kg:dream Workflow Specification

## Overview

Dream is the graph's self-review system. It surfaces candidates for human judgment:
- **Gray-zone same_as pairs** (pending edges at 0.85-0.95)
- **Recent-pair candidates** (nodes that might merge but no pending edge)
- **Expiring nodes** (time-limited facts/preferences approaching `valid_until`)
- **Orphan nodes** (facts with no edges to other entities)
- **Contradictions** (nodes with conflicting claims)

**Principle:** Dream proposes; harness decides. No implicit state advancement.

## Command Signatures

```bash
# Candidate inspection
kg dream candidates [--since <duration>] [--kind <type>] [--json]

# Verdict execution
kg review confirm <edge_id> --winner <node_id> [--reason <text>]
kg review reject <edge_id> [--reason <text>]
kg merge <winner_id> <loser_id> [--reason <text>]

# Queue management
kg dream status  # Show queue size, last review time
kg dream prune  # Remove stale candidates
```

## Input Formats

### Time-Bounded Queries

```bash
kg dream candidates --since 7d    # Last 7 days
kg dream candidates --since 12h   # Last 12 hours
kg dream candidates --since 30m   # Last 30 minutes
kg dream candidates --since 2026-07-20T00:00:00Z  # ISO timestamp
```

`--since` omitted: Scan **all active nodes** (T1 semantics).

### Kind Filtering

```bash
kg dream candidates --kind pending      # Gray-zone same_as only
kg dream candidates --kind recent-pair  # High-score node pairs
kg dream candidates --kind expiring     # Time-limited nodes
kg dream candidates --kind orphan       # Isolated facts
kg dream candidates --kind contradict    # Conflicting claims
```

## Processing Steps

### Step 1: Candidate Discovery

```bash
kg dream candidates --since 7d
```

Algorithm per kind:

#### Recent-Pair (default)

```sql
-- Find node pairs with high similarity but no pending same_as edge
SELECT n1.id, n2.id, similarity
FROM (
    SELECT n1.id, n2.id, vec_distance(n1.embedding, n2.embedding) AS similarity
    FROM nodes n1, nodes n2
    WHERE n1.id != n2.id
      AND n1.created_at >= ? OR n1.updated_at >= ?
      AND n2.created_at >= ? OR n2.updated_at >= ?
      AND n1.status = 'active' AND n2.status = 'active'
      AND NOT EXISTS (
        SELECT 1 FROM edges
        WHERE (source_id = n1.id AND target_id = n2.id AND semantic_type = 'same_as')
           OR (source_id = n2.id AND target_id = n1.id AND semantic_type = 'same_as')
      )
)
WHERE similarity >= 0.85
ORDER BY similarity DESC
LIMIT 100
```

Output:
```
recent-pair  score=0.93  nodes=u:person:x u:person:y  person pair (canonical 'X' vs 'Y')
```

#### Pending (Gray-zone)

```sql
-- Fetch existing pending same_as edges
SELECT e.id AS edge_id, e.source_id, e.target_id, e.confidence
FROM edges e
WHERE e.semantic_type = 'same_as'
  AND e.status = 'pending'
  AND (e.created_at >= ? OR e.updated_at >= ?)
ORDER BY e.confidence DESC
```

Output:
```
pending  score=0.89  edge=u:person:a|same_as|u:person:b  nodes=u:person:a u:person:b  gray-zone same_as
```

#### Expiring

```sql
-- Time-limited nodes approaching valid_until
SELECT id, type, name, valid_until,
       CAST((julianday(valid_until) - julianday('now')) * 24 AS REAL) AS hours_until
FROM nodes
WHERE valid_until IS NOT NULL
  AND valid_until > datetime('now')
  AND valid_until < datetime('now', '+7 days')
ORDER BY valid_until ASC
```

Output:
```
expiring  score=0.00  nodes=u:preference:editor-choice  expires in 2.3 days
```

#### Orphan

```sql
-- Facts with no edges to other entities
SELECT n.id, n.type, n.name
FROM nodes n
LEFT JOIN edges e ON (e.source_id = n.id OR e.target_id = n.id)
WHERE n.type = 'fact'
  AND e.id IS NULL
  AND n.status = 'active'
ORDER BY n.created_at DESC
```

Output:
```
orphan  score=0.00  nodes=u:fact:z  orphan
```

#### Contradict

```sql
-- Nodes with conflicting facts (same subject, predicate, different objects)
SELECT f1.id AS id1, f2.id AS id2,
       f1.attributes->>'subject' AS subject,
       f1.attributes->>'predicate' AS predicate
FROM nodes f1, nodes f2
WHERE f1.type = 'fact' AND f2.type = 'fact'
  AND f1.status = 'active' AND f2.status = 'active'
  AND f1.attributes->>'subject' = f2.attributes->>'subject'
  AND f1.attributes->>'predicate' = f2.attributes->>'predicate'
  AND f1.attributes->>'object' != f2.attributes->>'object'
  AND f1.id < f2.id  -- Avoid duplicates
```

Output:
```
contradict  score=0.00  nodes=u:fact:a u:fact:b  subject='AlphaFold', predicate='released_in'
```

### Step 2: Candidate Sorting

Deterministic order:
```
primary: reason (pending → recent-pair → expiring → orphan → contradict)
secondary: score DESC
tertiary: node_ids (lexicographic)
quaternary: edge_id (if present)
```

### Step 3: Verdict Decision Loop

For each candidate, harness decides:

#### Decision Tree (per kind)

| Kind | Verdict Options | Command | When to Use |
|---|---|---|---|
| **pending** | confirm | `kg review confirm <edge_id> --winner <node_id>` | You're sure they're the same |
| **pending** | reject | `kg review reject <edge_id>` | You're sure they're distinct |
| **pending** | keep | (none) | Not enough signal |
| **pending** | escalate | (none) | Need user input |
| **recent-pair** | merge | `kg merge <winner_id> <loser_id>` | Clear duplicate |
| **recent-pair** | keep | (none) | Not enough signal |
| **recent-pair** | escalate | (none) | Need user input |
| **expiring** | renew | (none, edit node) | Extend `valid_until` |
| **expiring** | expire | (none, let time pass) | Let it lapse |
| **orphan** | attach | `kg save --edges [...]` | Connect to graph |
| **orphan** | delete | (none, manual) | Remove dead fact |
| **contradict** | resolve | `kg review confirm <fact_edge> --winner <correct_id>` | One is correct |
| **contradict** | flag | (none) | Surface conflict |

### Step 4: Execute Verdict

#### Confirm (pending same_as)

```bash
kg review confirm u:person:a|same_as|u:person:b --winner u:person:a --reason "Same person, different casing"
```

Engine atomic transaction:
1. Validate edge is pending and winner is endpoint
2. Enrich winner with loser's aliases + sources
3. Repoint all edges from loser → winner
4. Tombstone loser (`status='tombstone'`)
5. Delete pending edge
6. Write audit line to `wiki/log.md`

On failure: Rollback all, error message.

#### Reject (pending same_as)

```bash
kg review reject u:person:a|same_as|u:person:b --reason "Different people"
```

Engine atomic transaction:
1. Validate edge is pending
2. Delete edge
3. Write audit line

#### Merge (recent-pair / direct)

```bash
kg merge u:person:x u:person:y --reason "Duplicate person entry"
```

Engine atomic transaction:
1. Validate types match, not same node
2. x (first arg) is winner
3. Enrich winner with loser's aliases + sources
4. Repoint all edges from loser → winner
5. Tombstone loser
6. Write audit line

### Step 5: Post-Verdict Sync

```bash
kg wiki sync
kg snapshot
```

- `kg wiki sync`: Regenerate affected entity pages
- `kg snapshot`: WAL-safe recovery artifact

Skip if no merges/confirms happened (all keeps/escalates).

## Output Artifacts

| Artifact | Location | Purpose |
|---|---|---|
| **Candidate list** | stdout (human or JSON) | Queue inspection |
| **Audit log** | `wiki/log.md` | Compliance/debug |
| **Graph mutations** | `kg.db` | Knowledge update |
| **Wiki pages** | `wiki/entities/<slug>.md` | Human-readable output |
| **Snapshot** | `.kg/snapshots/<version>.kg.zst` | Recovery point |

### Audit Log Schema (wiki/log.md)

```json
{"timestamp": "2026-08-01T00:00:00Z", "action": "review_confirm", "winner_id": "u:person:a", "loser_id": "u:person:b", "review_edge_id": "u:person:a|same_as|u:person:b", "winner_before": {...}, "loser_before": {...}, "edges_before": [...], "reason": "Same person, different casing"}
{"timestamp": "2026-08-01T00:01:00Z", "action": "merge", "winner_id": "u:person:x", "loser_id": "u:person:y", "review_edge_id": null, "winner_before": {...}, "loser_before": {...}, "edges_before": [...], "reason": "Duplicate person entry"}
```

## Integration Points

### codebase-memory-mcp Integration

When merging nodes that have code references:

```bash
kg merge u:object:authenticate-user u:object:user-auth --reason "Duplicate function"
```

Engine detects:
```json
{
  "attributes": {
    "codebase_ref": "codebase-memory://function:authenticate_user"
  }
}
```

Action:
1. Merge kg nodes
2. Call `mcp__codebase_memory__query_graph` to update cross-references
3. Surface code impact: "3 call sites updated"

### Graphify Integration

When merging conversation participants:

```bash
kg merge u:person:claude-ai u:person:claude --reason "Same entity in transcript"
```

Engine:
1. Updates kg nodes
2. Calls Graphify to re-index transcript mentions
3. Updates `mentions` edges in transcript subgraph

### AgentMemory Integration

When resolving contradictory session observations:

```bash
kg review confirm u:fact:x|contradicts|u:fact:y --winner u:fact:x --reason "X is from authoritative source"
```

Engine:
1. Marks Y as tombstone
2. Calls `mcp__plugin_agentmemory_agentmemory__memory_governance_delete` with Y's ID
3. Writes audit line linking both systems

### wiki Integration

After every confirm/merge:
```bash
kg wiki sync --entity <winner_id>  # Rebuild only affected page
```

After batch:
```bash
kg wiki build --from-dream  # Rebuild all touched entities
```

## Data Flow Diagram

```
┌──────────────────┐
│ kg dream         │  Inspect queue
│ candidates       │  --since, --kind filters
└──────┬───────────┘
       │
       ↓
┌──────────────────────┐
│ Candidate Discovery   │  SQL queries per kind
└──────┬───────────────┘
       │
       ↓
┌──────────────────────┐
│ Sorting & Dedup      │  Deterministic order
└──────┬───────────────┘
       │
       ↓
┌──────────────────────┐
│ Verdict Loop         │  For each candidate
└──────┬───────────────┘
       │
       ├─ confirm ───────────────────┐
       │                               ↓
       │                ┌──────────────────────────┐
       │                │  Enrich winner           │  aliases + sources
       │                │  Repoint edges           │  loser → winner
       │                │  Tombstone loser         │  status=tombstone
       │                │  Delete pending edge     │
       │                └──────────────────────────┘
       │
       ├─ reject ────────────────────┐
       │                               ↓
       │                ┌──────────────────────────┐
       │                │  Delete pending edge      │
       │                └──────────────────────────┘
       │
       ├─ merge ──────────────────────┐
       │                               ↓
       │                ┌──────────────────────────┐
       │                │  Validate types          │
       │                │  Enrich winner           │
       │                │  Repoint edges           │
       │                │  Tombstone loser         │
       │                └──────────────────────────┘
       │
       └─ keep / escalate ───────────┐
                                        ↓ (nothing written)
                         ┌──────────────────────────┐
                         │  Audit log write          │  wiki/log.md
                         └──────────────────────────┘
                                        ↓
                         ┌──────────────────────────┐
                         │  Wiki sync               │  affected entities
                         └──────────────────────────┘
                                        ↓
                         ┌──────────────────────────┐
                         │  Snapshot                │  .kg/snapshots/
                         └──────────────────────────┘
```

## Queue Management

### Pruning

```bash
kg dream prune  # Remove stale candidates
```

Rules:
- Recent-pair: If both nodes merged/deleted, remove
- Pending: If edge consumed, remove
- Expiring: If expired, auto-remove from queue
- Orphan: If attached, remove
- Contradict: If resolved, remove

### Status

```bash
kg dream status
```

Output:
```
Queue size: 47 candidates
  - pending: 12
  - recent-pair: 23
  - expiring: 8
  - orphan: 3
  - contradict: 1
Last review: 2026-08-01T00:00:00Z
Graph version: abc123...
```

## Error Handling

| Error | Action | User Message |
|---|---|---|
| **No .kg/** | Abort | "Run `kg init` first" |
| **Empty queue** | Return | "no candidates" |
| **Invalid --since** | Treat as None | "Invalid duration/timestamp; showing all candidates" |
| **Confirm on consumed edge** | Abort | "edge <id> not pending (already reviewed)" |
| **Type mismatch on merge** | Abort | "type mismatch: can't merge person into organization" |
| **Self-merge** | Abort | "cannot merge node with itself" |
| **Winner not endpoint** | Abort | "winner <id> is not an endpoint of edge <edge_id>" |

## Deferred Features (M2+)

- `kg dream --auto`: Automatic review for confidence ≥0.98
- `kg dream --explain`: Show why candidate surfaced
- `kg dream --export`: Dump queue to JSON for external review
- `kg dream --import`: Load external verdicts

## Dependencies

- **kg.db**: Pending edges, node similarity calculations
- **wiki/log.md**: Audit trail
- **wiki/**: Entity page regeneration
- **MCP servers**: codebase-memory, Graphify, AgentMemory for cross-system updates
