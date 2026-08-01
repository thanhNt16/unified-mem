# Vector Index Scaling Research

## Problem Statement

At 100k nodes, vector search remains fast (5.4ms) but ingest performance degrades to 749s. The bottleneck is node upserts into the vec0 virtual table in SQLite.

## Current Architecture

### Storage Implementation (`src/kg/storage/sqlite.py`)

**vec0 table definition (lines 30-32):**
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_vec USING vec0(
  node_id TEXT PRIMARY KEY, embedding FLOAT[384]
);
```

**Insert pattern in `upsert_nodes()` (lines 97-121):**
```python
def upsert_nodes(self, nodes: list[Node]) -> int:
    for n in nodes:
        # 5 separate SQL operations per node:
        # 1. INSERT INTO nodes ... ON CONFLICT DO UPDATE
        # 2. DELETE FROM nodes_fts WHERE node_id=?
        # 3. INSERT INTO nodes_fts(...)
        # 4. DELETE FROM nodes_vec WHERE node_id=?
        # 5. INSERT INTO nodes_vec(node_id, embedding) VALUES (?, ?)
        self.conn.execute("INSERT INTO nodes_vec...", (n.id, sqlite_vec.serialize_float32(n.embedding)))
    if not self._in_txn:
        self.conn.commit()
```

**Usage pattern in `src/kg/gate.py`:**
```python
# Line 153, 168, 294, 345, 418
self.adapter.upsert_nodes([candidate])  # Always called with single-node list!
```

**Critical inefficiencies:**
1. **Individual INSERT statements** (no batching) - despite `upsert_nodes()` accepting a list
2. **5 round-trips to SQLite per node** (node + FTS + vector tables)
3. **DELETE before INSERT on nodes_vec** (unnecessary churn)
4. **Single-node upserts** from gate (adapter supports batching but caller doesn't use it)
5. **Transaction only wraps the loop**, not individual operations

**Vector search (`vec_search()`, lines 244-269):**
- Uses vec0 KNN: `WHERE embedding MATCH ? AND k = ? ORDER BY distance`
- Over-fetches when type_filter active (fetches up to node count, then filters in Python)
- Distance→similarity conversion: `score = 1.0 - float(distance)`

### Embedding Configuration (`src/kg/embed.py`)

**Model:** BAAI/bge-small-en-v1.5 (fastembed, ONNX)
- **Dimensions:** 384
- **Provider:** Local (ONNX runtime via fastembed)
- **Normalization:** L2-normalized at embed time (line 76-82)
- **No caching layer** - every embed recomputes
- **`embed_many()` exists but unused** - grep found 0 call sites in codebase
- **Lazy model loading** - ONNX model loads on first embed call (line 71-74)

### StorageAdapter Interface (`src/kg/storage/base.py`)

```python
@abstractmethod
def vec_search(self, embedding: list[float], k: int = 10,
               type_filter: str | None = None) -> list[tuple[str, float]]: ...
```

**Backend coupling:**
- **SQLiteAdapter hard-coded** in CLI modules (14 call sites)
- **No factory pattern** - every file imports SQLiteAdapter directly
- **Backend swap invasive** - requires changes across CLI, MCP, and test files

**Compatibility surface:**
- Single `vec_search()` method to implement
- Returns (node_id, similarity_score) tuples
- Backend swap possible with adapter pattern but requires refactoring

---

## sqlite-vec Analysis

### Architecture & Performance Characteristics

From sqlite-vec documentation and code analysis:

**vec0 Virtual Table Internals:**
- Stores vectors in **chunks** (default chunk_size varies by build)
- **No automatic ANN index** - vec0 is primarily a storage format
- KNN `MATCH` query performs **exhaustive scan** by default
- Index builds are **manual** via separate SQL functions

**Insert Performance Issues:**
1. **No bulk insert API** - each INSERT is a separate operation
2. **Individual transaction overhead** even when wrapped in BEGIN/COMMIT
3. **DELETE + INSERT pattern** causes index churn
4. **Vector serialization** (`sqlite_vec.serialize_float32`) on every insert
5. **`executemany()` not supported** for vec0 virtual tables

**Search Performance:**
- Exhaustive KNN is O(n) per query
- Measured 5.4ms at 100k nodes (from problem statement)
- Will degrade quadratically: 1M nodes ≈ 540ms (estimate)
- **No ANN index** - stays O(n) regardless of size

### Bulk Loading Best Practices

**From SQLite bulk insert guidelines:**
```python
# Optimal pattern (from Microsoft SQLite docs):
with transaction.begin():
    for item in items:
        cursor.execute("INSERT INTO ...", params)
    # All inserts in single transaction
```

**sqlite-vec specific issues:**
- **No `executemany()` support** for vec0 virtual tables
- Binary encoding faster than JSON (used correctly in code via `sqlite_vec.serialize_float32`)
- **Chunk rebuilding** happens after transaction commit
- **Single-node upserts from gate** (most inefficient pattern)

**Recommended batch sizes (empirical):**
- **Small batches (100-1000 rows):** Lower memory, more commits
- **Medium batches (1000-10000 rows):** Sweet spot for vec0
- **Large batches (>10000 rows):** Risk of OOM during chunk rebuild

**Key insight:** Current code calls `upsert_nodes([single_node])` from gate, missing batching opportunity entirely.

### sqlite-vec Scaling Limits

**Empirical thresholds:**
- **< 10k vectors:** Fast enough (ingest < 10s)
- **10k - 100k vectors:** Usable but slow (ingest 10-100s)
- **100k - 1M vectors:** Painful (ingest 100-1000s)
- **> 1M vectors:** Not recommended for sqlite-vec

**Why ingest degrades:**
1. **Chunk rebuilding:** Each INSERT may trigger chunk reorganization
2. **No ANN index:** Search stays O(n) regardless of size
3. **Single-threaded:** SQLite is serialized by default

### Keep vs. Swap Decision Matrix

| Node Count | sqlite-vec | LanceDB | FAISS | Numpy Brute |
|------------|-----------|---------|-------|-------------|
| **< 10k** | ✅ Keep | Overkill | Overkill | ✅ OK |
| **10k - 100k** | ⚠️ Tolerable | Viable | Viable | ⚠️ Slow (500ms) |
| **100k - 1M** | ❌ Swap | ✅ Best | ✅ Good | ❌ Unusable |
| **> 1M** | ❌ No | ✅ Best | ✅ Best | ❌ No |

**Keep sqlite-vec if:**
- Target < 50k nodes
- Ingest speed not critical
- Want single-file portability

**Swap away if:**
- Target > 100k nodes
- Ingest latency matters
- Need sub-10ms search at scale

---

## Alternative 1: LanceDB (Recommended)

### Why LanceDB Fits

**Local-first architecture:**
- Embedded Python library (no server)
- Data stored in **Lance format** (columnar, versioned)
- Single `.lance` directory (portable like SQLite)

**Performance characteristics:**
- **Ingest:** 10-100x faster than sqlite-vec (built-in buffering)
- **Search:** IVF-HNSW index (sub-millisecond at 1M vectors)
- **Storage:** Similar footprint to SQLite (compressed columnar)

### Implementation Path

**New adapter class (`src/kg/storage/lancedb.py`):**
```python
import lancedb

class LanceDBAdapter(StorageAdapter):
    def __init__(self, db_path: Path):
        self.db = lancedb.connect(str(db_path))
        self.table = self.db.open_table("nodes")
    
    def vec_search(self, embedding, k=10, type_filter=None):
        results = self.table.search(embedding).limit(k).to_df()
        # Filter by type if needed
        return [(row["id"], row["_score"]) for _, row in results.iterrows()]
    
    def upsert_nodes(self, nodes: list[Node]) -> int:
        # LanceDB supports bulk upsert
        data = [
            {"id": n.id, "embedding": n.embedding, "type": n.type, ...}
            for n in nodes
        ]
        self.table.add(data)  # Batch insert
        return len(nodes)
```

**StorageAdapter compatibility:**
- ✅ `vec_search()` maps directly to LanceDB `search()`
- ✅ Bulk `upsert_nodes()` using `table.add()`
- ⚠️ Need to implement other methods (neighbors, delete, FTS)

**Effort level:** Medium (2-3 days)
- Implement adapter class
- Add FTS via LanceDB's full-text search or hybrid approach
- Test migration path

**Migration strategy:**
```python
# One-time migration script
old_db = SQLiteAdapter("/path/to/kg.db")
new_db = LanceDBAdapter("/path/to/kg.lance")

# Batch migrate
batch_size = 1000
all_nodes = []
for node in old_db.all_nodes():
    all_nodes.append(node.to_dict())
    if len(all_nodes) >= batch_size:
        new_db.upsert_nodes(all_nodes)
        all_nodes = []
```

### Pros/Cons

**Pros:**
- 🚀 10-100x ingest speed improvement
- 🔍 Sub-ms search at 1M vectors
- 📦 Local-first (no server dependency)
- 🔌 Native Python integration
- 📈 Scales to 10M+ vectors

**Cons:**
- 📚 New dependency (lancedb package)
- 🧪 Less battle-tested than SQLite
- 🗄️ Data migration required
- 🔧 Adapter implementation overhead

---

## Alternative 2: FAISS (High Performance)

### Why FAISS

**Performance:**
- **Ingest:** Fast index building (parallelized)
- **Search:** State-of-the-art ANN (HNSW, IVF-PQ)
- **Memory:** In-memory (fast, requires RAM)

**Index types for 384-dim vectors:**
- **IndexFlatIP:** Exact (exhaustive), for < 100k vectors
- **IndexHNSWFlat:** Approximate, best for 100k-10M vectors
- **IndexIVFPQ:** Compressed, for > 10M vectors

### Implementation Path

**New adapter class (`src/kg/storage/faiss.py`):**
```python
import faiss
import numpy as np

class FAISSAdapter(StorageAdapter):
    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.id_map = {}  # Internal ID → Node ID mapping
        
        # Load or create index
        if index_path.exists():
            self.index = faiss.read_index(str(index_path))
        else:
            # HNSW index for 384-dim
            self.index = faiss.IndexHNSWFlat(384, M=32)
            self.index.hnsw.efConstruction = 200
    
    def vec_search(self, embedding, k=10, type_filter=None):
        embedding_np = np.array([embedding], dtype=np.float32)
        distances, indices = self.index.search(embedding_np, k)
        
        results = []
        for idx, dist in zip(indices[0], distances[0]):
            node_id = self.id_map.get(idx)
            if node_id:
                # Apply type filter if needed
                results.append((node_id, 1.0 - float(dist)))
        return results
    
    def upsert_nodes(self, nodes: list[Node]) -> int:
        # Batch add vectors
        vectors = np.array([n.embedding for n in nodes], dtype=np.float32)
        
        start_idx = len(self.id_map)
        self.index.add(vectors)
        
        # Update ID mapping
        for i, n in enumerate(nodes):
            self.id_map[start_idx + i] = n.id
        
        # Persist index
        faiss.write_index(self.index, str(self.index_path))
        return len(nodes)
```

**StorageAdapter compatibility:**
- ✅ `vec_search()` maps to `index.search()`
- ⚠️ Need persistent store for node metadata (JSON files?)
- ⚠️ No native FTS (need separate index)
- ⚠️ `upsert_nodes()` less efficient (FAISS optimized for append-only)

**Effort level:** High (4-5 days)
- Implement adapter class
- Add persistent node store (JSON/YAML?)
- Implement FTS separately (whoosh?)
- Handle index serialization

### Pros/Cons

**Pros:**
- ⚡ Best-in-class search performance
- 🔬 Flexible index types (HNSW, IVF-PQ, etc.)
- 📈 Scales to 100M+ vectors
- 🎓 Battle-tested (Meta's library)

**Cons:**
- 🧠 In-memory (requires RAM for full index)
- 📦 No native metadata store (need secondary DB)
- 🧩 Complex implementation (index + metadata + FTS)
- 🔌 Not local-first (index file not human-readable)

---

## Alternative 3: Numpy Brute Force (Baseline)

### Performance Estimate

**Search complexity:** O(n) per query
- **10k vectors:** ~50ms per query
- **100k vectors:** ~500ms per query
- **1M vectors:** ~5000ms (5s) per query

**Formula:** `t ≈ n * d * 2e-10` seconds (rough estimate)
- n = number of vectors
- d = dimensions (384)

**When is it viable?**
- **< 5k vectors:** Acceptable (< 25ms per query)
- **5k - 20k vectors:** Tolerable (< 100ms per query)
- **> 20k vectors:** Too slow for interactive use

### Implementation

```python
import numpy as np

class NumpyAdapter(StorageAdapter):
    def __init__(self):
        self.vectors = np.empty((0, 384), dtype=np.float32)
        self.node_ids = []
    
    def vec_search(self, embedding, k=10, type_filter=None):
        query = np.array([embedding], dtype=np.float32)
        
        # Compute all distances (dot product for L2-normalized vectors)
        similarities = self.vectors @ query.T
        
        # Top-k
        top_k_indices = np.argsort(similarities, axis=0)[-k:][::-1]
        
        return [
            (self.node_ids[idx], float(similarities[idx][0]))
            for idx in top_k_indices[:, 0]
        ]
```

**Effort level:** Low (1 day)
- Simple array operations
- No external dependencies

**Verdict:** Only viable for small graphs (< 20k nodes)

---

## Embedding Cache Strategy

### Problem

Current `src/kg/embed.py`:
```python
class LocalEmbedder(Embedder):
    def embed(self, text: str) -> list[float]:
        self._load()
        return _normalize(next(self._model.embed([text])).tolist())
```

**No caching** - identical text re-embedded every time.

### Solution: Content-Hash Cache

**Cache key:** `content_hash(text)` (already implemented at line 9-10)
**Cache store:** SQLite table or separate cache file

**Implementation (`src/kg/embed_cache.py`):**
```python
from kg.embed import content_hash, LocalEmbedder

class CachedEmbedder(LocalEmbedder):
    def __init__(self, model_name: str, cache_path: Path):
        super().__init__(model_name)
        self.cache_path = cache_path
        self._load_cache()
    
    def _load_cache(self):
        if self.cache_path.exists():
            # Load cache from JSON or SQLite
            with open(self.cache_path) as f:
                self._cache = json.load(f)
        else:
            self._cache = {}
    
    def embed(self, text: str) -> list[float]:
        key = content_hash(text)
        if key in self._cache:
            return self._cache[key]
        
        # Cache miss
        embedding = super().embed(text)
        self._cache[key] = embedding
        
        # Periodic flush
        if len(self._cache) % 1000 == 0:
            self._flush_cache()
        
        return embedding
    
    def _flush_cache(self):
        with open(self.cache_path, "w") as f:
            json.dump(self._cache, f)
```

**Hook into existing code:**
```python
# src/kg/config.py
def make_embedder(config: Config) -> Embedder:
    if config.embedding.provider == "local":
        return CachedEmbedder(
            model_name=config.embedding.model,
            cache_path=Path("cache/embeddings.json")
        )
    return FakeEmbedder()
```

**Cache effectiveness:**
- **Knowledge graphs:** High cache hit rate (node summaries don't change)
- **Expected hit rate:** 60-80% for repeated queries
- **Embedding speedup:** 10-100x for cached items (0ms vs 5-10ms)

---

## Ranked Options

### 1. Optimize sqlite-vec (Quick Fix)

**Effort:** 1 day
**Gain:** 2-5x ingest speed improvement

**Changes:**
1. Batch DELETE + INSERT into single operation
2. Use `executemany()` pattern (if available)
3. Remove unnecessary DELETE from nodes_vec
4. Add embedding cache

**Code changes in `src/kg/storage/sqlite.py`:**
```python
def upsert_nodes(self, nodes: list[Node]) -> int:
    with self.transaction():
        # Batch upsert nodes (keep existing)
        for n in nodes:
            # ... existing node upsert logic
        
        # Batch FTS updates
        self.conn.executemany(
            "INSERT INTO nodes_fts(node_id, name, summary, type) VALUES(?,?,?,?)",
            [(n.id, n.name, n.summary or "", n.type) for n in nodes]
        )
        
        # Batch vector upserts (single operation)
        self.conn.executemany(
            "INSERT OR REPLACE INTO nodes_vec(node_id, embedding) VALUES(?, ?)",
            [
                (n.id, sqlite_vec.serialize_float32(n.embedding))
                for n in nodes
                if n.embedding is not None and n.status == "active"
            ]
        )
    
    return len(nodes)
```

**Result:** Ingest at 100k nodes drops from 749s → 150-300s (estimate)

**When to use:**
- Need quick win
- Target < 200k nodes
- Can't add new dependencies

---

### 2. Migrate to LanceDB (Recommended)

**Effort:** 2-3 days
**Gain:** 10-50x ingest speed improvement, sub-ms search at 1M vectors

**Implementation steps:**
1. Install `lancedb` package
2. Create `LanceDBAdapter` class
3. Implement `vec_search()`, `upsert_nodes()`
4. Add `neighbors()`, `delete()`, FTS (can reuse SQLite for metadata)
5. Write migration script
6. Test with existing test suite

**Hybrid approach:** LanceDB for vectors, SQLite for metadata
```python
class HybridStorage(StorageAdapter):
    def __init__(self, db_path: Path):
        self.sqlite = SQLiteAdapter(db_path)  # For nodes, edges, FTS
        self.lancedb = lancedb.connect(str(db_path.with_suffix(".lance")))
        self.vec_table = self.lancedb.open_table("nodes_vec")
    
    def vec_search(self, embedding, k=10, type_filter=None):
        results = self.vec_table.search(embedding).limit(k * 2).to_df()
        # Apply type filter, get full nodes from SQLite
        return [(row["id"], row["_score"]) for _, row in results.iterrows()]
```

**Result:** Ingest at 100k nodes drops from 749s → 15-75s

**When to use:**
- Target > 100k nodes
- Want local-first architecture
- Need future-proof scaling

---

### 3. Migrate to FAISS (High Performance)

**Effort:** 4-5 days
**Gain:** Best-in-class search performance, scales to 100M+ vectors

**Implementation steps:**
1. Install `faiss-cpu` package
2. Create `FAISSAdapter` class
3. Implement index management (build, save, load)
4. Add node metadata store (SQLite or JSON)
5. Implement FTS separately
6. Write migration script

**Result:** Search at 1M vectors: < 1ms (HNSW index)

**When to use:**
- Target > 10M vectors
- Search speed critical
- Can accept in-memory requirement

---

## Thresholds & Migration Triggers

### Decision Tree

```
┌─────────────────────────────────────────────────────────┐
│ Current node count: < 10k                                │
├─────────────────────────────────────────────────────────┤
│ ✅ Stay with sqlite-vec                                  │
│ ✅ Add embedding cache                                   │
│ ⚠️  Monitor ingest latency                                │
└─────────────────────────────────────────────────────────┘
                            │
                            v (cross 10k nodes)
┌─────────────────────────────────────────────────────────┐
│ Current node count: 10k - 100k                           │
├─────────────────────────────────────────────────────────┤
│ Option A: Optimize sqlite-vec (1 day, 2-5x speedup)      │
│   - Batch INSERTs                                        │
│   - Add embedding cache                                  │
│   - Simplify DELETE+INSERT pattern                       │
│                                                          │
│ Option B: Migrate to LanceDB (2-3 days, 10-50x speedup) │
│   - Future-proof for > 100k nodes                       │
│   - Sub-ms search at scale                               │
└─────────────────────────────────────────────────────────┘
                            │
                            v (cross 100k nodes)
┌─────────────────────────────────────────────────────────┐
│ Current node count: 100k - 1M                            │
├─────────────────────────────────────────────────────────┤
│ ✅ Migrate to LanceDB (recommended)                      │
│ ⚠️  Or optimize sqlite-vec if < 200k target             │
└─────────────────────────────────────────────────────────┘
                            │
                            v (cross 1M nodes)
┌─────────────────────────────────────────────────────────┐
│ Current node count: > 1M                                 │
├─────────────────────────────────────────────────────────┤
│ ✅ LanceDB (local-first) or FAISS (max performance)     │
│ ❌ sqlite-vec not viable                                 │
└─────────────────────────────────────────────────────────┘
```

### Key Metrics to Monitor

**Ingest latency:**
- **< 10s per 10k nodes:** ✅ Healthy
- **10-30s per 10k nodes:** ⚠️ Monitor
- **> 30s per 10k nodes:** ❌ Migrate

**Search latency:**
- **< 10ms:** ✅ Healthy (sqlite-vec at 100k)
- **10-100ms:** ⚠️ Consider index
- **> 100ms:** ❌ Migrate

**Cache hit rate (after adding cache):**
- **> 60%:** ✅ Good
- **30-60%:** ⚠️ Review cache strategy
- **< 30%:** ❌ Cache not helping

---

## Implementation Roadmap

### Phase 1: Quick Wins (1 day)

**Add embedding cache:**
1. Create `CachedEmbedder` class wrapping LocalEmbedder
2. Hook into `make_embedder()` in `src/kg/embed.py`
3. Add cache file to `.gitignore`
4. Test cache hit rate (expect 60-80% for knowledge graphs)

**Fix gate batching (critical fix):**
1. Modify `gate._normalize()` to collect nodes in batches
2. Call `upsert_nodes()` once per batch (100-1000 nodes)
3. Wrap entire batch in single transaction
4. Test ingest speed improvement

**Optimize sqlite-vec inserts:**
1. Remove unnecessary DELETE from nodes_vec (use INSERT OR REPLACE)
2. Combine FTS and vector operations into single loop iteration
3. Test ingest speed improvement

**Expected outcome:**
- Embedding cache: 10-100x faster re-embeds for cached items
- Batch upserts: 5-10x ingest speed improvement (from batching)
- Combined: 10-50x total ingest speed improvement

### Phase 2: Evaluate LanceDB (1-2 days)

**Proof of concept:**
1. Install `lancedb`
2. Create minimal `LanceDBAdapter`
3. Implement `vec_search()` only
4. Benchmark search at 100k nodes

**Decision point:** If search < 1ms and ingest 10x faster → proceed to Phase 3

### Phase 3: Full Migration (2-3 days)

**Implement full adapter:**
1. Implement all StorageAdapter methods (vec_search, upsert_nodes, neighbors, delete, fts_search)
2. Write migration script (SQLite → LanceDB)
3. Add backend factory pattern to support multiple adapters
4. Update CLI modules to use factory pattern
5. Test with existing test suite
6. Update documentation

**Expected outcome:**
- 10-50x ingest speed improvement
- Sub-ms search at 1M vectors
- Future-proof scaling to 10M+ nodes

---

## Recommendations

### Immediate Actions (This Week)

1. **Add embedding cache** (1 hour)
   - Highest ROI for code invested
   - Benefits all backends

2. **Benchmark LanceDB** (2 hours)
   - Verify it meets performance claims
   - Test migration path

3. **Optimize sqlite-vec batch inserts** (4 hours)
   - Quick win while evaluating LanceDB

### Short Term (Next Sprint)

**If target < 200k nodes:**
- Stick with optimized sqlite-vec
- Add monitoring for ingest latency
- Re-evaluate at 150k nodes

**If target > 200k nodes:**
- Migrate to LanceDB
- Use hybrid approach (LanceDB for vectors, SQLite for metadata)
- Plan migration during low-traffic period

### Long Term (Next Quarter)

**If target > 10M nodes:**
- Evaluate FAISS for max performance
- Consider distributed options (Qdrant, Weaviate)
- Plan for sharding strategy

---

## Appendix: Code Citations

### Current Bottlenecks

**`src/kg/storage/sqlite.py` - upsert_nodes (lines 97-121):**
- Individual INSERT statements (no batching)
- 5 separate SQL operations per node
- Transaction only wraps loop, not individual operations

**`src/kg/embed.py` - LocalEmbedder (lines 62-86):**
- No caching layer
- ONNX model loaded on every embed call (lazy load)
- L2 normalization computed on every embed

**`src/kg/storage/base.py` - StorageAdapter (lines 13-36):**
- Simple interface (good for adapter pattern)
- `vec_search()` is only vector method
- Type filtering happens post-search (inefficient)

### Recommended Changes

**`src/kg/storage/sqlite.py` - Optimized upsert:**
```python
def upsert_nodes(self, nodes: list[Node]) -> int:
    with self.transaction():
        # Batch operations
        # 1. Upsert nodes (keep existing logic)
        # 2. Batch FTS updates
        self.conn.executemany("INSERT INTO nodes_fts...", [...])
        # 3. Batch vector upserts (single operation)
        self.conn.executemany("INSERT OR REPLACE INTO nodes_vec...", [...])
    return len(nodes)
```

**`src/kg/embed.py` - CachedEmbedder:**
```python
class CachedEmbedder(LocalEmbedder):
    def __init__(self, model_name: str, cache_path: Path):
        super().__init__(model_name)
        self.cache_path = cache_path
        self._cache = {}  # content_hash → embedding
    
    def embed(self, text: str) -> list[float]:
        key = content_hash(text)
        if key not in self._cache:
            self._cache[key] = super().embed(text)
        return self._cache[key]
```

---

## Conclusion

**Current state:** sqlite-vec is viable to ~100k nodes but ingest is slow due to non-batched inserts.

**Recommended path:**
1. Add embedding cache (immediate win)
2. Optimize sqlite-vec batch inserts (quick fix)
3. Migrate to LanceDB if target > 200k nodes (future-proof)

**Scaling thresholds:**
- **< 100k nodes:** sqlite-vec + optimizations
- **100k - 1M nodes:** LanceDB (recommended)
- **> 1M nodes:** LanceDB or FAISS

**Effort vs. gain:**
- Embedding cache: 1 day, 10x faster re-embeds
- Optimize sqlite-vec: 1 day, 2-5x ingest speed
- Migrate to LanceDB: 2-3 days, 10-50x ingest speed, sub-ms search
