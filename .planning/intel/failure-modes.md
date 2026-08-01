# KG Failure Mode Analysis & Prevention Strategies

**Status:** Active analysis document. Updated: 2026-08-01

---

## Part 1: Failure Mode Analysis

### 1. Four Graphs Indexing Same Source (Duplication & Conflicts)

**How manifests in kg today:**
- Current kg is a single unified graph, but the threat pattern is real
- Codebase-memory-mcp already indexes code; kg indexes same sources; graphify would create third graph
- No cross-graph synchronization mechanism exists
- User could run multiple MCP servers against same `.kg/` directory
- Parallel extraction runs (race condition) could create duplicate nodes with same content-derived IDs

**Concrete prevention strategy:**
1. **Single source of truth enforcement:**
   - `raw/` directory is immutable (write-once by `kg raw add`)
   - Content-hash (sha256) deduplication in registry prevents duplicate ingestion
   - `kg save` uses content-derived IDs → idempotent writes

2. **Index lock file:**
   - `.kg/index.lock` created during `/kg:extract` runs
   - Concurrent extraction attempts block with clear error message
   - Lock includes PID and timestamp for stale detection

3. **Cross-graph coordination (future):**
   - `kg status --conflicts` checks for overlapping node_ids across indices
   - MCP server registration: only one kg MCP server per project
   - External graph sync: import/export APIs (not yet built)

**Validation/monitoring approach:**
```bash
# Check for duplicate extraction
kg dream candidates --kind recent-pair

# Verify graph integrity
kg status  # shows node/edge counts, pending reviews

# Manual cross-check: count unique sources vs nodes
kg raw list | wc -l  # sources
kg cypher "MATCH (n) RETURN count(DISTINCT n.sources[0].doc)"  # unique docs
```

**Monitoring metrics:**
- `recent-pair` candidates per dream run (target: <5% of nodes)
- Duplicate node_id collisions (target: 0)
- Registry entries vs unique sha256 ratio (target: 1.0)

---

### 2. Automatically Treating Session Statements as Facts (Authority Issues)

**How manifests in kg today:**
- `/kg:extract` uses harness LLM to extract POLE+O from `raw/conversations/*.md`
- Every user statement gets same extraction pass as PDF/docs
- No distinction between "user said" vs "user verified truth"
- LLM can hallucinate facts from casual conversation
- No authority weight (code > wiki > memory)

**Concrete prevention strategy:**
1. **Source authority tagging (REQUIRED):**
   - Every node/fact carries `sources[]` with origin
   - Authority levels: `code` (0.9), `documentation` (0.7), `conversation` (0.3), `inferred` (0.1)
   - Facts from `conversation` type get `confidence: low` automatically

2. **No auto-fact extraction from sessions (CHANGE):**
   - Session ingestion ONLY creates `conversation` nodes
   - User explicitly promotes session content to fact via `/kg:ingest --as-fact`
   - Never create `fact` nodes from `type: conversation` sources automatically

3. **Fact promotion workflow (see Part 3):**
   - Draft facts (from any source) → `wiki/facts/draft/`
   - Repetition counting: mentioned 3+ sessions → candidate
   - Manual review → promote to `fact` with `sources: [{type: verified}]`

4. **Conflict detection:**
   - Same subject+predicate with different objects = contradiction
   - `kg dream candidates --kind contradict` surfaces these
   - Higher authority wins (code > documentation > verified fact > conversation)

**Validation/monitoring approach:**
```bash
# Check low-confidence facts
kg cypher "MATCH (f:fact) WHERE f.sources[0].type='conversation' RETURN f"

# Find contradictions
kg dream candidates --kind contradict

# Authority distribution
kg cypher """
MATCH (n) RETURN n.sources[0].type, count(*)
"""
```

**Monitoring metrics:**
- Conversation-sourced facts % (target: <2%)
- Contradictions per dream run (target: 0 after resolution)
- Draft → promoted fact ratio (target: <10% promotion rate)

---

### 3. Letting Derived Graphs Become Unrebuildable (Provenance Loss)

**How manifests in kg today:**
- `kg.db` is designed as **derived state** (rebuildable from `raw/`)
- Registry tracks lineage: which raw file → which chunks → which nodes
- Current implementation maintains provenance correctly
- Risk: future features add nodes without lineage (e.g., inferred relationships)

**Concrete prevention strategy:**
1. **Lineage invariant (ENFORCED):**
   - Every node MUST have `sources[]` pointing to `{doc: raw/<path>, chunk: N}`
   - Every edge MUST have `sources[]` (inherited from endpoint nodes or explicit)
   - Gate validates lineage presence on save

2. **No orphan nodes:**
   - `kg wiki lint --orphan` checks for nodes without sources
   - Dream pass: `ORPHAN` candidate type surfaces nodes with broken lineage
   - Manual node creation prohibited via CLI (only via extract skill)

3. **Rebuild test:**
   - `kg verify --rebuild` test: delete `kg.db`, re-extract from `raw/`, compare hashes
   - CI runs rebuild test on snapshot changes
   - Any drift = test failure

4. **Immutable raw/ contract:**
   - `raw/` files never modified after ingest (append-only)
   - Content changes = new sha256 = new registry entry = re-extraction
   - Raw deletion cascades to node tombstoning (not silent loss)

**Validation/monitoring approach:**
```bash
# Verify rebuildability
kg verify --rebuild

# Check orphan nodes
kg wiki lint --orphan

# Trace node lineage
kg cypher "MATCH (n) WHERE n.sources = [] RETURN n"
```

**Monitoring metrics:**
- Orphan nodes count (target: 0)
- Rebuild hash mismatch (target: 0)
- Registry entries with missing raw files (target: 0)

---

### 4. Sending Full Graph into Context Window (Token Bloat)

**How manifests in kg today:**
- `kg pack_context` implements progressive disclosure (RRF ranking, budget truncation)
- Default 2-hop expansion
- Budget: 4000 tokens (configurable)
- Risk: user queries entire graph via `kg expand` without pack

**Concrete prevention strategy:**
1. **No "get all nodes" operation (DESIGN):**
   - `kg search` requires query string (no empty search)
   - `kg expand` requires seed node IDs (cannot expand from nothing)
   - Cypher queries limited to read-only subset with result caps

2. **Budget enforcement (RUNTIME):**
   - `kg pack` throws error if budget exceeded (fails closed)
   - Default expansion depth: 2 hops (config `query.default_hops`)
   - Session diversity filter: max 3 results from same source per session

3. **Progressive disclosure path:**
   - Pack returns ranked K (budget-fit)
   - If insufficient, user expands specific seeds (incremental)
   - Deep search materializes wiki instead of dumping to context

4. **Query cost estimation:**
   - `kg search --dry-run` shows estimated result count
   - Warn if expansion would exceed 10x budget
   - Suggest deep search for broad queries

**Validation/monitoring approach:**
```bash
# Test pack efficiency
kg pack --budget 4000 <subgraph.json>  # returns token count

# Monitor query costs
kg log query --since 1d  # shows tokens spent per query

# Detect runaway queries
kg log query --filter 'tokens > 8000'  # violations
```

**Monitoring metrics:**
- Pack tokens vs budget (target: <95% usage)
- Queries exceeding 2x budget (target: 0)
- Session token velocity (target: <10k/session median)

---

### 5. Using Memory Decay for Permanent Decisions (Data Loss)

**How manifests in kg today:**
- kg has NO memory decay mechanism implemented
- `valid_from`/`valid_until` exist on preferences/facts for temporal queries
- Risk: future "auto-cleanup" features tombstone old nodes
- Dream pass already surfaces `EXPIRING` candidates for review

**Concrete prevention strategy:**
1. **Permanent vs temporary distinction (SCHEMA):**
   - **Permanent:** nodes from `raw/` (code, docs, papers) → NEVER auto-deleted
   - **Temporary:** `conversation`, `session` nodes → configurable retention
   - **Time-bound:** preferences/facts with `valid_until` → expire to tombstone, not delete

2. **No auto-tombstone without review (POLICY):**
   - `kg dream --auto-cleanup` DISABLED by default
   - Expiring nodes surfaced as candidates → user decides
   - Snapshot required before any bulk tombstoning

3. **Archival workflow:**
   - `kg archive --before 2025-01-01` moves to `.kg/archive/` (not delete)
   - Archived nodes excluded from search but recoverable
   - Archive manifest records what+when+why

4. **Preference decay is intentional (FEATURE):**
   - Preferences evolve (user patterns change)
   - Old preference → `superseded_by` edge to new preference
   - Both retained for history; new preferred in queries

**Validation/monitoring approach:**
```bash
# Check expiring nodes (before auto-cleanup)
kg dream candidates --kind expiring

# Verify no permanent nodes were tombstoned
kg cypher """
MATCH (n) WHERE n.sources[0].type IN ('code', 'documentation')
  AND n.status='tombstoned'
RETURN count(n)
"""

# Archive integrity
kg archive --verify
```

**Monitoring metrics:**
- Permanent nodes tombstoned (target: 0)
- Conversation nodes retained beyond policy (warn)
- Archive recovery test success rate (target: 100%)

---

## Part 2: Authority Conflict Rules Enforcement

### Precedence Hierarchy

```
CODE (0.9) ──► WIKI (0.7) ──► VERIFIED_FACT (0.8) ──► DOCUMENTATION (0.7) ──► MEMORY (0.3)
              │                                  │
              └── CONVERSATION (0.3) ────────────┘
              └── INFERRED (0.1)
```

**Rules:**
1. **Code never conflicts:** code is source of truth for itself
2. **Wiki > conversation:** documentation consensus over casual chat
3. **Verified facts:** explicit user promotion trumps inference
4. **Inference loses:** derived relationships lowest priority

### Conflict Detection

**Definition:** Same node_id (after resolution) with conflicting property values.

**Implementation:**
```python
# kg/dream.py - CONTRADICT candidate type
def _find_contradictions(adapter, since_ts):
    # Fact pairs: same subject+predicate, different objects
    fact_conflicts = adapter.conn.execute("""
        WITH f1 AS (SELECT id, data FROM nodes WHERE type='fact' AND status='active'),
             f2 AS (SELECT id, data FROM nodes WHERE type='fact' AND status='active')
        SELECT f1.id as id1, f2.id as id2, f1.data as d1, f2.data as d2
        FROM f1, f2
        WHERE f1.id < f2.id
          AND json_extract(f1.data, '$.attributes.subject') = json_extract(f2.data, '$.attributes.subject')
          AND json_extract(f1.data, '$.attributes.predicate') = json_extract(f2.data, '$.attributes.predicate')
          AND json_extract(f1.data, '$.attributes.object') != json_extract(f2.data, '$.attributes.object')
    """).fetchall()

    # Node property conflicts (after resolution merge)
    node_conflicts = adapter.conn.execute("""
        SELECT n.id, n.data, json_each(n.data) as prop
        FROM nodes n
        WHERE n.status='active'
          AND json_array_length(n.sources) > 1
          AND EXISTS (SELECT 1 FROM json_each(n.data) WHERE key IN ('name', 'summary', 'type'))
    """).fetchall()

    return format_candidates(fact_conflicts + node_conflicts, "contradict")
```

**Conflict types:**
1. **Fact contradictions:** `Alice employed_by Google` vs `Alice employed_by Meta`
2. **Property contradictions:** node has `name: "NYC"` from one source, `name: "New York City"` from another (after resolution)
3. **Type contradictions:** node typed as `person` in one source, `location` in another

### Conflict Resolution

**Resolution strategies:**

| Strategy | When to use | Action |
|----------|------------|--------|
| **SUPersedes** | Newer version explicitly replaces old | Create `superseded_by` edge from old→new; mark old `valid_until = now` |
| **Stale** | Old source outdated | Mark old node `status: tombstoned`, keep edges |
| **Quarantine** | Both plausible, cannot auto-resolve | Create `conflict_with` edge; escalate to `review/contradictions.md` |
| **Authority wins** | Sources have different authority levels | Keep higher-authority version; tombstone lower |

**Implementation:**
```python
# kg/gate.py - conflict resolution on save
def _resolve_conflict(self, existing_node, new_node):
    auth_existing = self._authority_score(existing_node.sources[0])
    auth_new = self._authority_score(new_node.sources[0])

    if auth_new > auth_existing + 0.2:  # Authority gap >0.2
        # Authority wins: tombstone existing
        self._tombstone(existing_node.id, reason=f"superseded_by {new_node.id}")
        return "MERGED", new_node.id

    elif abs(auth_new - auth_existing) < 0.2:
        # Authority gap <0.2: quarantine for review
        edge = Edge(
            id=edge_id(new_node.id, "conflict_with", existing_node.id),
            source=new_node.id,
            target=existing_node.id,
            semantic_type="conflict_with",
            summary=f"Property conflict: {self._diff_summary(existing_node, new_node)}",
            confidence=0.0,
            sources=new_node.sources + existing_node.sources
        )
        self.adapter.upsert_edges([edge])
        return "QUARANTINED", None

    else:
        # Existing authority higher: reject new
        return "REJECTED", existing_node.id
```

**Conflict tracking:**
- All conflicts logged to `wiki/log.md` with both node versions
- Dream pass surfaces unresolved conflicts as `CONTRADICT` candidates
- Manual resolution via `kg resolve --winner <id> --loser <id> --strategy superseded`

### Validation & Monitoring

```bash
# Find all conflicts
kg dream candidates --kind contradict

# Authority distribution check
kg cypher """
MATCH (n) RETURN n.sources[0].type as authority, count(*)
ORDER BY count(*) DESC
"""

# Conflict resolution rate
kg log conflict --resolved --since 30d
```

**Metrics:**
- Unresolved conflicts count (target: <10)
- Authority-based auto-resolutions % (target: >80%)
- Manual escalations (target: <20%)

---

## Part 3: Promotion Workflow (AgentMemory → Wiki)

### Confidence Thresholds

**Draft → Candidate → Verified fact pipeline:**

| Stage | Trigger | Confidence | Location | Action |
|-------|---------|-------------|----------|--------|
| **Draft** | First mention | 0.1-0.3 | `wiki/facts/draft/` | LLM suggests fact; human review required |
| **Candidate** | 3+ mentions OR authority boost | 0.4-0.6 | `wiki/facts/candidate/` | Auto-surface for review |
| **Verified** | Explicit promotion OR 0.7+ confidence | 0.7-1.0 | `wiki/facts/verified/` | Promoted to main graph as `fact` node |

**Confidence scoring:**
```python
# kg/fact_promotion.py
def confidence_score(fact, mentions, sources):
    base = 0.0

    # Source authority
    source_weights = {"code": 0.4, "documentation": 0.3, "conversation": 0.1}
    base += max(source_weights.get(s.type, 0.0) for s in sources)

    # Repetition counting
    mention_count = len(mentions)
    if mention_count >= 5:
        base += 0.3
    elif mention_count >= 3:
        base += 0.2
    elif mention_count >= 2:
        base += 0.1

    # Source diversity (mentioned in 3+ sessions/docs)
    unique_sources = len(set(m.source for m in mentions))
    if unique_sources >= 3:
        base += 0.2
    elif unique_sources >= 2:
        base += 0.1

    # No contradictions
    if has_contradiction(fact):
        base -= 0.3

    return min(base, 1.0)
```

### Repetition Counting

**Implementation:**
```python
# kg/dream.py - repetition detection
def _count_mentions(adapter, fact_pattern):
    """Find similar facts across sources."""
    subject, predicate = fact_pattern["subject"], fact_pattern["predicate"]

    # BM25 search for subject+predicate phrases
    mentions = adapter.conn.execute("""
        SELECT id, data, sources FROM nodes
        WHERE type='fact'
          AND attributes.subject MATCH ?
          AND attributes.predicate MATCH ?
    """, (subject, predicate)).fetchall()

    # Group by source (sessions/docs)
    by_source = {}
    for m in mentions:
        src = m["sources"][0]["doc"] if m["sources"] else "unknown"
        by_source[src] = by_source.get(src, 0) + 1

    return {
        "total": len(mentions),
        "unique_sources": len(by_source),
        "by_source": by_source
    }
```

**Repetition thresholds:**
- **3+ unique sources** → auto-promote to candidate
- **5+ mentions total** → confidence +0.3
- **Same fact in code + docs** → confidence +0.2 (source diversity)

### Review Criteria

**Automatic promotion (no review):**
- Confidence ≥0.9 (high authority, high repetition)
- Code-sourced facts with 2+ documentation confirmations
- No contradictions exist

**Manual review queue:**
- Confidence 0.4-0.7 → surface in `review/facts.md`
- Contradicted facts → require conflict resolution first
- Draft facts >7 days old → auto-delete (stale draft)

**Review process:**
```bash
# List candidates
kg dream candidates --kind candidate

# Promote to verified
kg promote --fact <fact-id> --to verified --reason "repeated 5x across sessions"

# Reject draft
kg promote --fact <fact-id> --to rejected --reason "contradicted by code source"
```

### Wiki Integration

**Draft → Wiki workflow:**
1. **Draft creation:** `/kg:extract` with `--draft-facts` flag creates `wiki/facts/draft/<subject>-<predicate>.md`
2. **Candidate promotion:** Dream pass moves qualified drafts to `wiki/facts/candidate/`
3. **Verified promotion:** Manual confirmation creates `fact` node in kg.db, copies to `wiki/facts/verified/`
4. **Wiki sync:** `kg wiki sync` regenerates entity pages from verified facts

**File structure:**
```
wiki/
├── facts/
│   ├── draft/           # LLM-suggested, not verified
│   ├── candidate/       # 3+ mentions, pending review
│   └── verified/        # Human-promoted, in graph
└── entities/
    └── <entity>.md      # Includes verified facts section
```

### Monitoring & Metrics

```bash
# Promotion funnel
kg stats promotion  # shows draft→candidate→verified counts

# Stale drafts
kg find draft --older-than 7d

# Fact quality
kg cypher """
MATCH (f:fact) RETURN f.sources[0].type, count(*)
"""
```

**Metrics:**
- Draft promotion rate (target: >10% drafts → candidates)
- Candidate promotion rate (target: >50% candidates → verified)
- Stale draft cleanup (target: <20 drafts >7 days)
- Verified fact contradiction rate (target: <5%)

---

## Appendix: Validation Checklists

### Pre-Commit Checklist (for kg developers)

- [ ] No nodes created without `sources[]` lineage
- [ ] No auto-fact extraction from `type: conversation` sources
- [ ] All conflicts resolved or quarantined
- [ ] Pack budget enforced on all query paths
- [ ] No auto-tombstone of permanent nodes
- [ ] Authority scores computed on conflict resolution

### Pre-Release Checklist (for kg maintainers)

- [ ] Rebuild test passes (`kg verify --rebuild`)
- [ ] No orphan nodes (`kg wiki lint --orphan`)
- [ ] Conflict rate baseline established
- [ ] Promotion workflow tested end-to-end
- [ ] Performance: pack <100ms for 100-node subgraph
- [ ] Snapshot included in release

### Operational Checklist (for kg users)

- [ ] Run `kg dream` weekly to catch conflicts
- [ ] Review `wiki/facts/candidate/` before promotion
- [ ] Backup `raw/` and `registry.jsonl` to git
- [ ] Monitor `kg stats promotion` for drift
- [ ] Archive old conversations quarterly
- [ ] Verify rebuildability after schema changes

---

**Document status:** Living document. Update when prevention strategies implemented or new failure modes discovered.
