# 100k-node ingest scaling

## Scope and interpretation

The supplied 100k measurement matches the direct storage path in `bench/scale_100k.py`, not `Gate.normalize()`: the benchmark constructs nodes without `embedding` and calls `adapter.upsert_nodes(nodes)` in nominal 5,000-row batches (`bench/scale_100k.py:26-34`). Therefore embedding generation and `Deduper` cannot explain that 740s result. They are separate, worse blockers for any gate-mediated 100k ingest.

## Root cause

### Confirmed: each SQL statement autocommits on the benchmark path

`SQLiteAdapter` opens SQLite with `isolation_level=None`, explicitly documenting autocommit (`src/kg/storage/sqlite.py:44-49`). `upsert_nodes()` does not open `transaction()` (`src/kg/storage/sqlite.py:97-121`), so its final `conn.commit()` cannot turn preceding statements into one transaction. For every node it issues:

1. `INSERT ... ON CONFLICT` into `nodes` (`src/kg/storage/sqlite.py:100-105`)
2. `DELETE` from FTS (`src/kg/storage/sqlite.py:106-108`)
3. `INSERT` into FTS5 (`src/kg/storage/sqlite.py:109-112`)
4. `DELETE` from vec0 (`src/kg/storage/sqlite.py:113`)
5. conditional vec0 `INSERT` (`src/kg/storage/sqlite.py:114-118`)

Thus the benchmark's apparent 5,000-row batch is 400k committed write statements for nodes lacking embeddings, not 20 transactions. FTS5 maintenance occurs one document at a time as its corpus grows. This is the strongest code-supported explanation for the 10x workload producing ~8x higher per-node latency.

`transaction()` is sound when called: it executes one `BEGIN IMMEDIATE`/`COMMIT`, suppresses inner commits via `_in_txn` (`src/kg/storage/sqlite.py:77-92`). `Gate.normalize()` does use it (`src/kg/gate.py:98-99`); direct adapter callers do not.

### Confirmed secondary costs

- No `executemany`: every node produces four/five Python `execute()` calls (`src/kg/storage/sqlite.py:98-118`). This is fixed per-node interpreter/SQLite API overhead, not proof of super-linear growth by itself.
- Each node serializes its full Pydantic model with `model_dump_json()` (`src/kg/storage/sqlite.py:94-100`). `Node` includes the embedding list (`src/kg/ontology.py:24-41`), making this material at 384 dimensions. It is linear CPU cost, not a demonstrated source of the scale curve.
- FTS5 receives delete-then-insert even for fresh IDs (`src/kg/storage/sqlite.py:106-112`). The delete is redundant for known-new bulk loads. Incremental FTS5 segment maintenance is plausible, but not isolated by current measurements; treat as hypothesis pending the experiment.
- vec0 is deleted for every node even if it has no embedding (`src/kg/storage/sqlite.py:113-118`). The supplied benchmark nodes have none (`bench/scale_100k.py:29-32`), so there are no vec0 inserts in that measurement. vec0 insert complexity is unconfirmed here.

### Confirmed: gate ingest has independent quadratic paths

`Gate._normalize()` handles nodes one-by-one (`src/kg/gate.py:112-172`), computes a candidate embedding (`src/kg/gate.py:140-141`), then calls `Deduper.dedup()` (`src/kg/gate.py:142`). `full_context_embedding()` always calls `embedder.embed()`; no cache or batch API (`src/kg/dedup.py:43-46`). `Deduper.dedup()` immediately embeds the candidate again (`src/kg/dedup.py:63`), then re-embeds every candidate to score it (`src/kg/dedup.py:89-96`). `embed_many()` exists for both fake/local embedders (`src/kg/embed.py:55-56`, `src/kg/embed.py:80-82`) but is unused in this path.

More seriously, the alleged indexed canonical-name lookup selects *all* active nodes of a type (`src/kg/dedup.py:69-75`), Pydantic-deserializes each JSON blob (`src/kg/dedup.py:77-85`), then compares in Python. It does not query `canonical_name`; despite index `idx_nodes_type_canonical` (`src/kg/storage/sqlite.py:34`), this is O(nodes-of-type) per candidate, O(N²) across a homogeneous import.

`vec_search(..., type_filter=...)` also calls `count()` then requests every vector (`fetch_k = max(k, count)`) and filters by fetching/deserializing nodes in Python (`src/kg/storage/sqlite.py:244-269`). For gate imports with embeddings this adds another O(N) per node. It is not exercised by the supplied direct benchmark, because those nodes have no vectors.

## Ranked fixes

| Rank | Change | Expected gain | Blast radius / risk |
|---|---|---:|---|
| 1 | Make `upsert_nodes()` own one explicit transaction when `_in_txn` is false, or require callers to use `with adapter.transaction()` around each 5,000-node chunk. Keep chunks bounded. | 10-100x direct-load gain; removes ~400k transaction commits in the measured run. Measure locally; durability mode/device determine exact result. | Low. Atomicity changes from per statement to per chunk; a failure rolls back the chunk. Existing Gate path already has this semantic. |
| 2 | Build row tuples once; use `executemany()` separately for `nodes`, FTS delete, FTS insert, vec delete, vec insert. Skip FTS/vec delete for declared-new IDs. | 1.5-5x after rank 1; fewer Python/SQLite crossings. SQLite docs specify `executemany()` repeatedly executes one DML statement, so it is a throughput improvement, not a deferred-index solution. | Medium. Upsert semantics, tombstones, and mixed new/update batches need equivalence tests. Do not apply before rank 1. |
| 3 | Add a bulk-load mode: insert base `nodes` first; populate/rebuild FTS after load. Prefer a staging table plus one FTS population statement. | 2-10x if FTS segment churn dominates; unconfirmed. | High. Search must not observe partial ingestion; rebuild/error recovery, updates, deletes, and FTS consistency require explicit contract. |
| 4 | Gate path: replace canonical-name scan with `WHERE status='active' AND type=? AND canonical_name=?`, selecting only IDs. Add/use an index matching that predicate if query-plan measurement needs it. | Changes homogeneous gate ingest from O(N²) canonical matching to near O(N log N); likely orders of magnitude at 100k. | Medium. `canonical_name` normalization semantics must remain exact; migrated rows have backfilled values (`src/kg/storage/sqlite.py:58-75`). |
| 5 | Gate path: compute each candidate embedding once, pass it into dedup; batch initial candidate embeddings with `embed_many()`. Cache/reuse stored candidate embeddings during scoring; avoid type-filtered full-vector fetch. | Fake embedder: modest. Local ONNX: likely 2-10x gate gain, hardware/model dependent. | Medium-high. Requires a dedup API change and exact-score equivalence tests. Vector type filtering needs an SQL/vector design; do not silently reduce candidate recall. |

## Experiment: isolate before changing code

Create a standalone `bench/profile_ingest.py` (measurement-only; do not modify production code) that runs fresh temp DBs at N = 1k, 10k, 100k and prints elapsed seconds, ms/node, DB size, FTS row count, vec row count.

Run these cases with identical generated `Node`s and `time.perf_counter()`:

1. Current `adapter.upsert_nodes(nodes)` in 5k chunks, no embedding.
2. Same, wrapped in `with adapter.transaction():` per 5k chunk.
3. Case 2 with all `embedding=None`, then all 384-dim fake vectors; report the vec increment.
4. Direct base-table-only reference using the same JSON payloads in a transaction; then add FTS operations; then vec operations. This attributes storage cost without changing app code.
5. Separate Gate-only runs at 1k/10k with a counting embedder and SQLite trace callback. Report embed calls, `SELECT ... WHERE status='active' AND type=?` count, vector rows fetched, and ms/node.

Acceptance signal: rank 1 should flatten direct-load ms/node substantially. If case 2 remains super-linear, case 4 identifies FTS versus vec0. Gate results must be reported separately; direct adapter benchmarks do not validate Gate ingest.
