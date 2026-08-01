# Scale Viz: 100k Node Latency Root Cause & Fixes

## Measured Problem
- **100k nodes**: `/clusters.json` = 9756ms, `/graph.json` = 9635ms
- **10k nodes**: `/clusters.json` = 579ms, `/graph.json` = 603ms
- **Scaling factor**: ~16x latency for 10x data = super-linear = O(n²) hotspot

## Root Cause Code Citations

### 1. `louvain()` Recomputed Per HTTP Request (Primary Bottleneck)
**Location**: `src/kg/viz/server.py`
- Line 128 in `_graph_payload()`: `clusters = louvain(adapter)`
- Line 189 in `_cluster_payload()`: `clusters = louvain(adapter)`
- **Issue**: No cache. Called on EVERY request to `/graph.json` and `/clusters.json`

**Location**: `src/kg/community.py` (lines 20-77)
- Lines 20-32: Loads ALL active nodes + edges from SQL
- Lines 40-42: Builds full NetworkX graph
- Lines 44-51: Runs Louvain algorithm (O(n²) worst-case)
- Lines 60-71: Per-component Louvain at scale >5000 nodes
- **Estimated cost**: ~8.5s at 100k nodes (graph construction + algorithm)

### 2. `_cluster_payload()` Iterates All Edges in Python
**Location**: `src/kg/viz/server.py` (lines 200-208)
```python
erows = adapter.conn.execute(
    "SELECT source, target FROM edges WHERE status='active'"
).fetchall()
for r in erows:
    cs, ct = clusters.get(r["source"]), clusters.get(r["target"])
    if cs is None or ct is None or cs == ct:
        continue
    key = (min(cs, ct), max(cs, ct))
    inter_edges[key] = inter_edges.get(key, 0) + 1
```
- **Issue**: Fetches ALL edges (300k-400k at 100k nodes), iterates in Python
- **Estimated cost**: ~0.4s at 100k nodes

### 3. `_graph_payload()` Scans Edges for Degree Count
**Location**: `src/kg/viz/server.py` (lines 133-152)
```python
deg: dict[str, int] = {}
e_rows = adapter.conn.execute(
    "SELECT source, target, data FROM edges WHERE status='active' ORDER BY id"
).fetchall()
for r in e_rows:
    s, t = r["source"], r["target"]
    if s not in active_ids or t not in active_ids:
        continue
    deg[s] = deg.get(s, 0) + 1
    deg[t] = deg.get(t, 0) + 1
    try:
        data = json.loads(r["data"])  # JSON parse per row
```
- **Issue**: Scans ALL edges, JSON parses each, even though only 2000 nodes returned
- **Estimated cost**: ~0.8s at 100k nodes

### 4. No Degree Precomputation
**Location**: `src/kg/storage/sqlite.py` (lines 12-36)
- Schema has no `degree` column or index
- Must compute on-the-fly via edge scan
- **Missing**: Materialized `node_degree` column updated on edge insert/delete

### 5. No HTTP Caching
**Location**: `src/kg/viz/server.py` (lines 275-286, 310, 319)
- `do_GET()` sends JSON but no `ETag`, `Last-Modified`, or `Cache-Control`
- Repeated identical requests recompute everything
- **Missing**: ETag based on `(node_count, edge_count, schema_version)` hash

## Profile Breakdown (100k nodes estimate)

| Operation | Time | % of Total |
|-----------|------|------------|
| `louvain()` (graph + algorithm) | ~8.5s | 87% |
| `_graph_payload` edge scan + JSON | ~0.8s | 8% |
| `_cluster_payload` Python iteration | ~0.4s | 4% |
| JSON serialization | ~0.1s | 1% |
| **Total** | **~9.8s** | **100%** |

## Ranked Fixes (Expected Gain)

### 🔥 Fix #1: Cache `louvain()` Result — **85% latency reduction** (~9.7s → ~1.5s)

**Implementation** (2-3h):
```python
# In kg/viz/server.py
from functools import lru_cache
import hashlib

@lru_cache(maxsize=1)
def _louvain_cached(db_path: str, snapshot_key: str) -> dict[str, int]:
    adapter = SQLiteAdapter(Path(db_path))
    try:
        return louvain(adapter)
    finally:
        adapter.conn.close()

def _snapshot_key(adapter: StorageAdapter) -> str:
    """Hash of node/edge counts + last mod time for invalidation."""
    stats = adapter.count()
    mod = adapter.conn.execute(
        "SELECT MAX(updated) AS m FROM nodes"  # Assuming `updated` column exists
    ).fetchone()
    return f"{stats['nodes']}:{stats['edges']}:{mod['m'] or 0}"

# In _graph_payload / _cluster_payload:
key = _snapshot_key(adapter)
clusters = _louvain_cached(str(adapter.db_path), key)
```

**Invalidation**: When nodes/edges change (via `save` skill or direct upsert), update `updated` timestamp or increment a `generation` counter.

**Acceptance test**:
```python
# tests/test_viz_cache.py
def test_louvain_cache_hit_second_request():
    adapter = _build_100k_graph()
    t1 = time.perf_counter()
    _graph_payload(adapter)  # cold: ~9.7s
    cold_ms = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    _graph_payload(adapter)  # hot: cached
    hot_ms = (time.perf_counter() - t2) * 1000

    assert hot_ms < cold_ms * 0.2  # hot < 20% of cold
    assert hot_ms < 2000  # sub-2s on cache hit
```

**Edge case**: Multi-process concurrency — use memcached or SQLite table `kv_store` for cache if LRU cache insufficient.

---

### 🔥 Fix #2: SQL Aggregation for Cluster Inter-Edges — **4% latency reduction** (~9.7s → ~9.3s after fix #1: ~1.5s → ~1.1s)

**Implementation** (1-2h):
```python
# In _cluster_payload (replace lines 200-208):
cluster_map = adapter.conn.execute(
    "SELECT id, cluster FROM _clusters"  # Precomputed cluster table
).fetchall()
nid_to_cid = {r["id"]: r["cluster"] for r in cluster_map}

inter_edges = adapter.conn.execute(
    """
    SELECT
        MIN(c1.cluster) AS ca,
        MAX(c1.cluster) AS cb,
        COUNT(*) AS weight
    FROM edges e
    JOIN _clusters c1 ON c1.id = e.source
    JOIN _clusters c2 ON c2.id = e.target
    WHERE e.status = 'active'
      AND c1.cluster IS NOT NULL
      AND c2.cluster IS NOT NULL
      AND c1.cluster != c2.cluster
    GROUP BY ca, cb
    ORDER BY weight DESC
    """
).fetchall()
```

**Prerequisite**: Fix #1 must also write `louvain()` output to SQLite table `_clusters(id, cluster)` (materialized view updated on save).

**Acceptance**: `_cluster_payload` at 100k nodes completes < 1.5s (down from ~9.8s).

---

### Fix #3: Precompute Node Degree Column — **8% latency reduction** (~9.7s → ~8.9s before fix #1)

**Implementation** (3-4h):
1. Schema migration:
```sql
ALTER TABLE nodes ADD COLUMN degree INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_nodes_degree ON nodes(degree);
```

2. Update on edge insert/delete (in `SQLiteAdapter.upsert_edges`):
```python
# After upsert
self.conn.execute(
    "UPDATE nodes SET degree = degree + 1 WHERE id = ?", (src,)
)
self.conn.execute(
    "UPDATE nodes SET degree = degree + 1 WHERE id = ?", (tgt,)
)
```

3. Query in `_graph_payload` (replace lines 133-152):
```python
node_rows = adapter.conn.execute(
    "SELECT id, data, degree FROM nodes WHERE status='active' AND id IN ({})"
    .format(",".join("?"*len(active_ids))),
    list(active_ids)
).fetchall
```

**Acceptance**: `_graph_payload` no longer contains `SELECT ... FROM edges` for degree.

---

### Fix #4: HTTP ETag / 304 Not Modified — **0% latency (100% bandwidth savings) for unchanged graphs**

**Implementation** (2h):
```python
def _etag(adapter: StorageAdapter) -> str:
    stats = adapter.count()
    return hashlib.md5(f"{stats['nodes']}:{stats['edges']}".encode()).hexdigest()

# In do_GET for /graph.json and /clusters.json:
etag = f'"{_etag(adapter)}"'
if_none_match = self.headers.get("If-None-Match")
if if_none_match == etag:
    self.send_response(304)
    self.end_headers()
    return
self._send(200, body, "application/json", extra={"ETag": etag})
```

**Acceptance**: Browser re-fetch sends `If-None-Match`, server responds 304 + empty body (2ms vs 9700ms).

---

### Fix #5: Pagination `/graph.json` (Offset/Cursor) — **No latency gain; enables incremental loading**

**Implementation** (4h):
- Add query params `?offset=0&limit=2000` (default `limit=2000`)
- Persist `_graph_payload` cursor across requests via session token
- Browser loads first 2000, user scrolls → fetch next batch
- **Trade-off**: Loses global degree/cluster context (only computed over current page)

**Acceptance**: `/graph.json?offset=2000&limit=2000` returns next 2000 nodes in < 200ms.

---

## Acceptance Gate

### One-Line Research/Proposal
**Fix #1 (cache `louvain`) reduces 100k-node latency from 9.7s to ~1.5s (85% gain)**. Combined with Fix #2 (SQL aggregation) → **sub-second viz at 100k nodes**.

### Test Plan
1. Run `tests/test_scale_10k.py::test_scale_10k_cluster_payload` at 10k, 50k, 100k
2. Assert:
   - `/clusters.json` < 1.5s at 100k nodes
   - `/graph.json` < 2s at 100k nodes
   - Cache hit on second request < 200ms
   - ETag returns 304 on unchanged graph

### Rollout Risk
- **Low**: Caching key based on counts is deterministic; worst case = stale cache cleared on process restart
- **Medium**: Fix #3 (degree column) requires migration + backfill (one-time cost at startup for existing DBs)
- **Low**: HTTP ETag uses standard header; browser compat universal

### Next Step
Implement Fix #1 first (cache `louvain`) — highest ROI, lowest complexity. Validate with 100k-scale benchmark before proceeding to Fix #2.
