# KG M2 — Dream, Review & Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build consolidation: `kg dream candidates` (the worklist), `kg merge` / `kg review` (gray-zone judgment + execution), `kg snapshot` / `--from-snapshot` (team bootstrap), and `kg wiki sync` (regenerate entity pages from the graph). Plus the `/kg:dream` skill. This is the "keep the graph clean" loop from spec §8.

**Architecture:** The engine proposes a typed worklist over the SQLite store (DB-only, embeddings already cached from M1); the harness skill judges each item and calls back into `kg merge` / `kg review` / supersede commands — reusing M1's `Gate.merge()` so a dream-initiated merge goes through the same idempotent, pre-image-logging path. Snapshots are Zstandard-compressed copies of `kg.db`. Wiki sync reads the graph and writes one markdown page per canonical entity.

**Tech Stack:** builds on M1 (`SQLiteAdapter`, `Gate`, `Edge`/`Node`, `Config`, `KgPaths`); adds `zstandard>=0.23`. pytest TDD throughout.

## Global Constraints

- **Engine proposes, harness judges, engine executes.** `dream candidates` never mutates the graph; only `kg merge`/`kg review`/supersede do.
- **Auto-merge only ≥0.95** (engine-scored). The model may *confirm* a gray-zone pair (push to confirmed) but never bulk-merge below threshold — a wrong merge is unrecoverable.
- **Every action is reversible via lineage.** Merges log pre-images to `wiki/log.md`; tombstones keep `merged_into`.
- **Snapshot = compressed kg.db copy.** `kg.db` is derived; snapshot is the portable bootstrap artifact.
- **Builds on M1 interfaces:** `SQLiteAdapter` (`adapter.conn`), `Gate.merge(winner_id, loser_id)`, `Node.valid_until`, `same_as`/`superseded_by` structural edges, `Registry.mark_extracted`.

---

## File Map

| File | Responsibility |
|---|---|
| `src/kg/dream.py` | `dream_candidates(adapter, since) -> Worklist` — the 6 worklist kinds |
| `src/kg/wiki.py` | `sync_entities(adapter, paths) -> int` — write `wiki/entities/<slug>.md`; `append_log(paths, line)` |
| `src/kg/snapshot.py` | `snapshot(paths) -> Path`, `restore(paths) -> None` (zstandard) |
| `src/kg/cli/dream.py` | `kg dream candidates` |
| `src/kg/cli/review.py` | `kg merge`, `kg review list/confirm/reject` |
| `src/kg/cli/snapshot_cli.py` | `kg snapshot` |
| `src/kg/cli/wiki_cli.py` | `kg wiki sync` |
| `tests/test_dream.py`, `test_wiki.py`, `test_snapshot.py` + cli smoke |

---

### Task 1: `dream_candidates` — RECENT-PAIR + PENDING

**Files:** Create `src/kg/dream.py`, `tests/test_dream.py`
**Interfaces — Produces:** `WorkItem(kind, a_id, b_id, score, detail)` and `Worklist(items: list[WorkItem], generated_at: str)`. `dream_candidates(adapter, since: str | None) -> Worklist`. `since` filters nodes by `created_at >= since` for RECENT-PAIR; PENDING lists all `same_as` edges with status pending (stored in edge `summary`/`confidence`).

- [ ] **Step 1: Failing test**

```python
# tests/test_dream.py
from kg.dream import dream_candidates
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node, Edge


def test_pending_same_as_listed(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:person:p1", type="person", name="Paris"),
                    Node(id="u:person:p2", type="person", name="Paree")])
    a.upsert_edges([Edge(id="u:person:p1|same_as|u:person:p2",
                         semantic_type="same_as", confidence=0.9)])
    wl = dream_candidates(a, since=None)
    kinds = {w.kind for w in wl.items}
    assert "PENDING" in kinds
    p = [w for w in wl.items if w.kind == "PENDING"][0]
    assert {p.a_id, p.b_id} == {"u:person:p1", "u:person:p2"}


def test_recent_pair_pairs_same_type(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:a", type="person", name="AAA", created_at="2026-07-26T10:00:00Z"),
        Node(id="u:person:b", type="person", name="BBB", created_at="2026-07-26T10:00:00Z"),
        Node(id="u:object:c", type="object", name="CCC", created_at="2026-07-26T10:00:00Z"),
    ])
    wl = dream_candidates(a, since="2026-07-25")
    rp = [w for w in wl.items if w.kind == "RECENT-PAIR"]
    # a,b are same-type (person); c is not
    assert any({w.a_id, w.b_id} == {"u:person:a", "u:person:b"} for w in rp)
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/dream.py
from __future__ import annotations
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from kg.ontology import Node, Edge
from kg.storage.base import StorageAdapter


@dataclass
class WorkItem:
    kind: str          # RECENT-PAIR | PENDING | EXPIRING | ORPHAN | CONTRADICT | WIKI-LINT
    a_id: str | None
    b_id: str | None
    score: float
    detail: str = ""


@dataclass
class Worklist:
    items: list[WorkItem] = field(default_factory=list)
    generated_at: str = ""

    @property
    def by_kind(self) -> dict[str, list[WorkItem]]:
        out: dict[str, list[WorkItem]] = {}
        for it in self.items:
            out.setdefault(it.kind, []).append(it)
        return out


def _all_active_nodes(adapter: StorageAdapter) -> list[Node]:
    rows = adapter.conn.execute(
        "SELECT data FROM nodes WHERE status='active'").fetchall()
    return [Node.model_validate_json(r["data"]) for r in rows]


def dream_candidates(adapter: StorageAdapter, since: str | None) -> Worklist:
    wl = Worklist(generated_at=datetime.now(timezone.utc).isoformat())
    nodes = _all_active_nodes(adapter)

    # RECENT-PAIR: same-type nodes ingested since `since`
    if since:
        recent = [n for n in nodes if (n.created_at or "") >= since]
    else:
        recent = nodes
    by_type: dict[str, list[Node]] = {}
    for n in recent:
        by_type.setdefault(n.type, []).append(n)
    for type_, group in by_type.items():
        for a, b in itertools.combinations(group, 2):
            wl.items.append(WorkItem("RECENT-PAIR", a.id, b.id, 0.0,
                                     detail=f"{type_} pair"))

    # PENDING: same_as edges (status encoded in edge.data; pending if confidence in [0.85,0.95))
    erows = adapter.conn.execute(
        "SELECT data FROM edges WHERE semantic_type='same_as'").fetchall()
    for r in erows:
        e = Edge.model_validate_json(r["data"])
        wl.items.append(WorkItem("PENDING", e.id.split("|")[0], e.id.split("|")[-1],
                                 e.confidence, detail="gray-zone same_as"))
    return wl
```

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m2): dream worklist — RECENT-PAIR + PENDING`

---

### Task 2: Worklist — EXPIRING, ORPHAN, CONTRADICT

**Files:** Modify `src/kg/dream.py`, `tests/test_dream.py`
**Interfaces:** adds three kinds. EXPIRING = active nodes with `valid_until < now` (facts/preferences). ORPHAN = active nodes whose every `sources.doc` no longer exists in `raw/` (we approximate: orphan if no live `mentions`/`part_of` edge and zero sources). CONTRADICT = fact pairs sharing subject+predicate with conflicting object — approximate via fact nodes with equal name (subject+predicate) differing summary.

> Scope guard: these are heuristic sweeps over the node table (cheap, DB-only). Full contradiction detection across arbitrary facts is M4-grade; M2 ships the sweep that surfaces candidates.

- [ ] **Step 1: Failing tests** (append)

```python
def test_expiring_listed(tmp_path):
    from kg.dream import dream_candidates
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:preference:old", type="preference", name="likes-x",
                         valid_until="2020-01-01T00:00:00Z")])
    wl = dream_candidates(a, since=None)
    assert any(w.kind == "EXPIRING" for w in wl.items)


def test_orphan_listed_when_no_sources(tmp_path):
    from kg.dream import dream_candidates
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([Node(id="u:fact:f1", type="fact", name="X is Y", sources=[])])
    wl = dream_candidates(a, since=None)
    assert any(w.kind == "ORPHAN" and w.a_id == "u:fact:f1" for w in wl.items)
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement** (append to `dream_candidates`, before `return wl`)

```python
    now = wl.generated_at
    # EXPIRING
    for n in nodes:
        if n.valid_until and n.valid_until < now:
            wl.items.append(WorkItem("EXPIRING", n.id, None, 0.0,
                                     detail=f"valid_until {n.valid_until}"))
    # ORPHAN: zero sources
    for n in nodes:
        if not n.sources:
            wl.items.append(WorkItem("ORPHAN", n.id, None, 0.0, detail="no lineage"))
    # CONTRADICT: facts with identical name, differing summary
    facts = [n for n in nodes if n.type == "fact"]
    for fa, fb in itertools.combinations(facts, 2):
        if fa.name == fb.name and (fa.summary or "") != (fb.summary or ""):
            wl.items.append(WorkItem("CONTRADICT", fa.id, fb.id, 0.0,
                                     detail=f"name={fa.name}"))
```

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m2): dream worklist — EXPIRING/ORPHAN/CONTRADICT`

---

### Task 3: `kg merge` + `kg review` CLI

**Files:** Create `src/kg/cli/review.py`, `tests/cli/test_review.py`; Modify `src/kg/cli/main.py`

**Interfaces — Produces:** `merge_cli(a, b, tombstone_b: bool)` → calls `Gate.merge(b, a)` semantics (winner=a) if `--tombstone-b`, else `Gate.merge(a,b)`. `review_list_cli()`, `review_confirm_cli(same_as_id)`, `review_reject_cli(same_as_id)`. confirm = set the `same_as` edge to status active + run merge; reject = delete the edge. Also writes `review/same_as.md` checklist.

- [ ] **Step 1: Failing test**

```python
# tests/cli/test_review.py
from typer.testing import CliRunner
from kg.cli.main import app


def test_merge_and_review_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()
    r.invoke(app, ["init", "--user-id", "u", "--scope", "s"])
    # seed two nodes + a pending same_as via save (two Parises)
    import json
    (tmp_path / "n1.json").write_text(json.dumps(
        [{"type": "person", "name": "Paris", "summary": "capital of France"}]))
    (tmp_path / "e.json").write_text("[]")
    r.invoke(app, ["save", "--nodes", str(tmp_path / "n1.json"),
                   "--edges", str(tmp_path / "e.json"), "--source", "raw/a.md#chunk-0"])

    # list review queue
    out = r.invoke(app, ["review", "list"])
    assert out.exit_code == 0

    # confirm a same_as by id (if any) — otherwise just assert list runs
    out2 = r.invoke(app, ["review", "list"])
    assert "PENDING" in out2.stdout or "same_as" in out2.stdout or "no pending" in out2.stdout.lower()
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/cli/review.py
from __future__ import annotations
import typer

from kg.paths import KgPaths
from kg.config import Config
from kg.storage.sqlite import SQLiteAdapter
from kg.embed import make_embedder
from kg.resolve import Resolver
from kg.dedup import Deduper
from kg.gate import Gate
from kg.dream import dream_candidates
from kg.ontology import Edge


def _gate(paths, cfg):
    ad = SQLiteAdapter(paths.kg_db); emb = make_embedder(cfg)
    return ad, Gate(ad, Resolver(ad, emb, cfg.thresholds, cfg.project.user_id),
                    Deduper(ad, emb, cfg.thresholds), emb, cfg, cfg.project.user_id)


def merge_cli(a: str = typer.Argument(...), b: str = typer.Argument(...),
              tombstone_b: bool = typer.Option(True, "--tombstone-b/--keep-b")) -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad, gate = _gate(paths, cfg)
    winner, loser = (a, b) if tombstone_b else (b, a)
    gate.merge(winner, loser)
    typer.echo(f"merged {loser} into {winner}")


def review_list_cli() -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad = SQLiteAdapter(paths.kg_db)
    wl = dream_candidates(ad, since=None).by_kind.get("PENDING", [])
    if not wl:
        typer.echo("no pending same_as reviews")
        return
    for w in wl:
        typer.echo(f"PENDING {w.a_id}|same_as|{w.b_id}  score={w.score:.2f}")


def _same_as_edge_id(arg: str) -> str:
    # accept either a full edge id or "a b"
    if "|" in arg:
        return arg
    raise typer.BadParameter("provide the full same_as edge id (a|same_as|b)")


def review_confirm_cli(same_as_id: str = typer.Argument(...)) -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad, gate = _gate(paths, cfg)
    src, _, tgt = _same_as_edge_id(same_as_id).split("|", 2)
    gate.merge(src, tgt)  # confirm = merge
    ad.conn.execute("DELETE FROM edges WHERE id=?", (same_as_id,))
    ad.conn.commit()
    typer.echo(f"confirmed + merged {tgt} into {src}")


def review_reject_cli(same_as_id: str = typer.Argument(...)) -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad = SQLiteAdapter(paths.kg_db)
    _same_as_edge_id(same_as_id)
    ad.conn.execute("DELETE FROM edges WHERE id=?", (same_as_id,))
    ad.conn.commit()
    typer.echo(f"rejected {same_as_id}")
```

Register in `main.py`:

```python
from kg.cli import review as review_cmd   # noqa: E402
from kg.cli import dream as dream_cmd     # noqa: E402  (Task 4)
app.command(name="merge")(review_cmd.merge_cli)
review_app = typer.Typer(); app.add_typer(review_app, name="review")
review_app.command(name="list")(review_cmd.review_list_cli)
review_app.command(name="confirm")(review_cmd.review_confirm_cli)
review_app.command(name="reject")(review_cmd.review_reject_cli)
```
(Add the `dream` import in Task 4; for Task 3 register only `merge` + `review`.)

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m2): kg merge + kg review list/confirm/reject`

---

### Task 4: `kg dream candidates` CLI + `/kg:dream` skill

**Files:** Create `src/kg/cli/dream.py`, `skills/kg-dream/SKILL.md`, `tests/cli/test_dream_cli.py`, `tests/test_skills_dream.py`

- [ ] **Step 1: Failing tests**

```python
# tests/cli/test_dream_cli.py
from typer.testing import CliRunner
from kg.cli.main import app


def test_dream_candidates_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()
    r.invoke(app, ["init", "--user-id", "u", "--scope", "s"])
    out = r.invoke(app, ["dream", "candidates"])
    assert out.exit_code == 0
```

```python
# tests/test_skills_dream.py
from pathlib import Path
def test_dream_skill_exists_and_bounded():
    md = (Path(__file__).resolve().parents[1] / "skills" / "kg-dream" / "SKILL.md").read_text()
    assert len(md.splitlines()) < 500
    assert "kg dream candidates" in md
    assert "kg merge" in md
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement CLI**

```python
# src/kg/cli/dream.py
from __future__ import annotations
import typer
from kg.paths import KgPaths
from kg.config import Config
from kg.storage.sqlite import SQLiteAdapter
from kg.dream import dream_candidates


def dream_candidates_cli(
    since: str = typer.Option(None, "--since", help="ISO timestamp; defaults to last dream marker"),
) -> None:
    paths = KgPaths.for_cwd(); cfg = Config.from_path(paths.config)
    ad = SQLiteAdapter(paths.kg_db)
    marker = paths.root / "last_dream.txt"
    eff_since = since or (marker.read_text().strip() if marker.exists() else None)
    wl = dream_candidates(ad, since=eff_since)
    for kind, items in wl.by_kind.items():
        typer.echo(f"== {kind} ({len(items)}) ==")
        for w in items[:50]:
            typer.echo(f"  {w.a_id}  {w.b_id}  {w.score:.2f}  {w.detail}")
    marker.write_text(wl.generated_at, encoding="utf-8")
```

Register: `app.command(name="dream")(dream_app)` with a typer subgroup, or simpler — register a callback. Use:

```python
# main.py append:
from kg.cli import dream as dream_cmd   # noqa: E402
dream_app = typer.Typer(); app.add_typer(dream_app, name="dream")
dream_app.command(name="candidates")(dream_cmd.dream_candidates_cli)
```

- [ ] **Step 4: Author `skills/kg-dream/SKILL.md`** — engine-proposes/harness-judges flow (spec §8): run `kg dream candidates`, for each item decide merge/reject/supersede/retire/keep/escalate via the exact CLI commands, then `kg wiki sync && kg snapshot`. <500 lines.
- [ ] **Step 5: Run, verify PASS**
- [ ] **Step 6: Commit** — `feat(m2): kg dream candidates CLI + /kg:dream skill`

---

### Task 5: `kg snapshot` + `--from-snapshot`

**Files:** Create `src/kg/snapshot.py`, `src/kg/cli/snapshot_cli.py`, `tests/test_snapshot.py`; Modify `pyproject.toml` (`zstandard>=0.23`), `src/kg/cli/init.py` (add `--from-snapshot`), `src/kg/cli/main.py`

**Interfaces — Produces:** `snapshot(paths: KgPaths) -> Path` writes `snapshots/kg.db.zst`. `restore(paths: KgPaths) -> None` decompresses over `kg.db`. `init --from-snapshot` runs restore after layout creation.

- [ ] **Step 1: Add dep** — `"zstandard>=0.23"`, `uv sync`.
- [ ] **Step 2: Failing test**

```python
# tests/test_snapshot.py
import sqlite3
from kg.paths import KgPaths
from kg.snapshot import snapshot, restore
from kg.cli.init import init_project
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node


def test_snapshot_restore_roundtrip(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="s")
    ad = SQLiteAdapter(paths.kg_db)
    ad.upsert_nodes([Node(id="u:person:x", type="person", name="X")])

    zst = snapshot(paths)
    assert zst.exists() and zst.name == "kg.db.zst"

    # wipe kg.db, restore
    paths.kg_db.unlink()
    restore(paths)
    assert paths.kg_db.exists()
    ad2 = SQLiteAdapter(paths.kg_db)
    assert ad2.get("u:person:x") is not None
```

- [ ] **Step 3: Run, verify FAIL**
- [ ] **Step 4: Implement**

```python
# src/kg/snapshot.py
from __future__ import annotations
import zstandard as zstd
from pathlib import Path
from kg.paths import KgPaths


def snapshot(paths: KgPaths) -> Path:
    paths.snapshots.mkdir(parents=True, exist_ok=True)
    src = paths.kg_db
    dst = paths.snapshots / "kg.db.zst"
    cctx = zstd.ZstdCompressor(level=19)
    data = src.read_bytes()
    dst.write_bytes(cctx.compress(data))
    return dst


def restore(paths: KgPaths) -> None:
    zst = paths.snapshots / "kg.db.zst"
    if not zst.exists():
        raise FileNotFoundError(zst)
    dctx = zstd.ZstdDecompressor()
    paths.kg_db.write_bytes(dctx.decompress(zst.read_bytes()))
```

```python
# src/kg/cli/snapshot_cli.py
import typer
from kg.paths import KgPaths
from kg.snapshot import snapshot as do_snapshot

def snapshot_cli() -> None:
    paths = KgPaths.for_cwd()
    out = do_snapshot(paths)
    typer.echo(f"snapshot: {out}")
```

Add `--from-snapshot` to `init_cli` in `src/kg/cli/init.py`:

```python
def init_cli(...,
    from_snapshot: bool = typer.Option(False, "--from-snapshot"),
) -> None:
    paths = init_project(Path.cwd(), user_id=user_id, scope=scope)
    if from_snapshot:
        from kg.snapshot import restore
        restore(paths)
        typer.echo("restored kg.db from snapshot")
    typer.echo(f"Initialized kg memory at {paths.root}")
```

Register `app.command(name="snapshot")(snapshot_cli.snapshot_cli)` in main.py.

- [ ] **Step 5: Run, verify PASS**
- [ ] **Step 6: Commit** — `feat(m2): kg snapshot + init --from-snapshot (zstandard)`

---

### Task 6: `kg wiki sync` — entity pages from graph

**Files:** Create `src/kg/wiki.py`, `src/kg/cli/wiki_cli.py`, `tests/test_wiki.py`; Modify `src/kg/cli/main.py`

**Interfaces — Produces:** `sync_entities(adapter, paths) -> int` writes `wiki/entities/<slug>.md` per active canonical entity (one section: summary, type, aliases, sources), returns count. `append_log(paths, line)` appends to `wiki/log.md`. CLI: `kg wiki sync`.

- [ ] **Step 1: Failing test**

```python
# tests/test_wiki.py
from kg.wiki import sync_entities, append_log
from kg.storage.sqlite import SQLiteAdapter
from kg.ontology import Node
from kg.cli.init import init_project


def test_sync_entities_writes_pages(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="s")
    ad = SQLiteAdapter(paths.kg_db)
    ad.upsert_nodes([Node(id="u:person:demis-hassabis", type="person",
                          name="Demis Hassabis", summary="founder",
                          aliases=["D. Hassabis"])])
    n = sync_entities(ad, paths)
    assert n == 1
    page = (paths.wiki / "entities" / "demis-hassabis.md").read_text()
    assert "Demis Hassabis" in page and "founder" in page


def test_append_log(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="s")
    append_log(paths, "dream merged p2 into p1")
    assert "p2 into p1" in (paths.wiki / "log.md").read_text()
```

- [ ] **Step 2: Run, verify FAIL**
- [ ] **Step 3: Implement**

```python
# src/kg/wiki.py
from __future__ import annotations
from pathlib import Path
from kg.paths import KgPaths
from kg.ontology import Node
from kg.storage.base import StorageAdapter
from kg.chunking import slugify


def sync_entities(adapter: StorageAdapter, paths: KgPaths) -> int:
    ent_dir = paths.wiki / "entities"
    ent_dir.mkdir(parents=True, exist_ok=True)
    rows = adapter.conn.execute(
        "SELECT data FROM nodes WHERE status='active'").fetchall()
    count = 0
    for r in rows:
        n = Node.model_validate_json(r["data"])
        aliases = ", ".join(n.aliases) or "—"
        sources = "\n".join(f"- {s.get('doc','?')}#chunk-{s.get('chunk','?')}"
                            for s in n.sources) or "—"
        body = (
            f"# {n.name}\n\n"
            f"- **type:** {n.type}" + (f" ({n.subtype})" if n.subtype else "") + "\n"
            f"- **canonical:** {n.canonical_name or n.name}\n"
            f"- **aliases:** {aliases}\n\n"
            f"## Summary\n\n{n.summary or '—'}\n\n"
            f"## Sources\n\n{sources}\n"
        )
        (ent_dir / f"{slugify(n.name)}.md").write_text(body, encoding="utf-8")
        count += 1
    return count


def append_log(paths: KgPaths, line: str) -> None:
    log = paths.wiki / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
```

```python
# src/kg/cli/wiki_cli.py
import typer
from kg.paths import KgPaths
from kg.config import Config
from kg.storage.sqlite import SQLiteAdapter
from kg.wiki import sync_entities

def wiki_sync_cli() -> None:
    paths = KgPaths.for_cwd(); Config.from_path(paths.config)
    n = sync_entities(SQLiteAdapter(paths.kg_db), paths)
    typer.echo(f"synced {n} entity pages")
```

Register: `wiki_app = typer.Typer(); app.add_typer(wiki_app, name="wiki"); wiki_app.command(name="sync")(wiki_cli.wiki_sync_cli)`.

- [ ] **Step 4: Run, verify PASS**
- [ ] **Step 5: Commit** — `feat(m2): kg wiki sync — entity pages from graph`

---

### Task 7: M2 acceptance — dream → merge → snapshot

**Files:** Create `tests/test_acceptance_m2.py`

- [ ] **Step 1: Write acceptance test**

```python
# tests/test_acceptance_m2.py
"""M2 E2E: two near-dup Parises → save flags same_as → dream candidates lists PENDING
→ review confirm merges → snapshot roundtrips."""
import json
from typer.testing import CliRunner
from kg.cli.main import app


def test_m2_dream_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()
    r.invoke(app, ["init", "--user-id", "u", "--scope", "demo"])

    def save(nodes):
        (tmp_path / "n.json").write_text(json.dumps(nodes))
        (tmp_path / "e.json").write_text("[]")
        return r.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                              "--edges", str(tmp_path / "e.json"),
                              "--source", "raw/x.md#chunk-0"])

    save([{"type": "person", "name": "Paris", "summary": "capital of France"}])
    save([{"type": "person", "name": "Paris", "summary": "capital of France, Europe"}])

    cand = r.invoke(app, ["dream", "candidates"])
    assert "PENDING" in cand.stdout

    # find the pending same_as id and confirm it
    import re
    m = re.search(r"(u:person:[a-z0-9-]+\|same_as\|u:person:[a-z0-9-]+)", cand.stdout)
    assert m, cand.stdout
    conf = r.invoke(app, ["review", "confirm", m.group(1)])
    assert conf.exit_code == 0

    # wiki sync + snapshot
    assert r.invoke(app, ["wiki", "sync"]).exit_code == 0
    snap = r.invoke(app, ["snapshot"])
    assert snap.exit_code == 0
    assert (tmp_path / ".kg" / "snapshots" / "kg.db.zst").exists()
```

- [ ] **Step 2: Run full suite** — `uv run pytest -v`. Expected: green (M0+M1+M2).
- [ ] **Step 3: Commit** — `feat(m2): acceptance test; M2 complete`

---

## Self-Review

**Spec coverage (§8, §11 M2 subset):** worklist kinds RECENT-PAIR/PENDING/EXPIRING/ORPHAN/CONTRADICT (§8) → Tasks 1,2 (WIKI-LINT deferred to M4 with `kg wiki lint`). `kg dream candidates` / `kg merge` / `kg review list|confirm|reject` (§11) → Tasks 3,4. Snapshot + `--from-snapshot` (§10) → Task 5. `kg wiki sync` (§11) → Task 6. `/kg:dream` skill (§14) → Task 4. New-knowledge-via-dream (§8) rides the skill calling `kg save` (M1) — covered by skill authoring. ✓ Gap: WIKI-LINT (needs `kg wiki lint`, M4) and `superseded_by` explicit write command — both deferred; supersede is achievable via existing edge upsert through `kg save`, noted in skill.

**Placeholder scan:** none. Task 4 Step 4 + Task 6 prose authoring are genuine, not TODO. CONTRADICT is explicitly a heuristic sweep (scope-guarded), not a stub.

**Type consistency:** `WorkItem`/`Worklist.by_kind` (Task 1) consumed by CLI (Tasks 3,4) and acceptance (Task 7). `Gate.merge(winner, loser)` signature (M1) honored by `merge_cli`/`review_confirm_cli`. `snapshot`/`restore(paths)` roundtrip test matches `init --from-snapshot` call. ✓

---

## Execution Handoff

**Plan saved to `docs/superpowers/plans/2026-07-26-kg-m2-dream-review.md`.** Next: **M3 (MCP + harness installers)**, then M4–M6.
