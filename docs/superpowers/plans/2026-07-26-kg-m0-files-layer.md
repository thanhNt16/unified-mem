# KG M0 — Files Layer Implementation Plan

> ✅ **COMPLETE — 2026-07-26.** All 12 tasks implemented, 41/41 tests passing,
> real CLI verified end-to-end (init → raw add → dedupe → list → status → config).
> Commits `ea4b5e3`…`aa8f792`. Three plan bugs caught and fixed inline via TDD:
> tiktoken encoding `cl100k_base`, structural-vs-semantic edge sets, and
> converter type-detection + title-from-H1. Next: M1 (graph + normalization gate).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]` → `- [x]`) syntax for tracking.

**Goal:** Build the local-first files layer of `kg` — `kg init`, `kg raw add`, `kg raw list`, `kg status`, `kg config get` — so a project gets a working, git-friendly LLM-wiki memory with immutable raw sources, a registry, and an index. No graph, no vectors, no embeddings yet (those are M1).

**Architecture:** One Python package `kg` (Road A). `core` modules (`config`, `paths`, `ontology`, `frontmatter`, `chunking`, `convert`, `registry`) hold all logic and are imported by nothing outside themselves; `cli` is a thin Typer shell that calls core functions and renders output. Every source is normalized to a markdown file with YAML frontmatter under `.kg/raw/`, registered in `.kg/registry.jsonl` deduped by sha256, and chunk boundaries are precomputed so M1's extraction can resume deterministically.

**Tech Stack:** Python ≥3.11, `uv` (project + tool install), Typer (CLI), Pydantic v2 (config + ontology), tiktoken `cl100k` (chunking), PyYAML (frontmatter), markitdown (pdf/docx/html→md), trafilatura (URL→md), pytest (TDD).

## Global Constraints

- **Python floor:** `requires-python = ">=3.11"`.
- **Local-first:** everything lives under `<project>/.kg/`. No network on any code path except the URL converter's fetch.
- **Git policy (spec §3):** commit `.kg/raw`, `.kg/wiki`, `.kg/registry.jsonl`, `.kg/ontology.json`, `.kg/config.toml`; **ignore `.kg/kg.db`** (and `snapshots/` until M2, but include the dir). `.gitignore` entries are created in Task 1.
- **Immutability:** files under `.kg/raw/` are write-once. Re-ingesting identical content (same sha256) is a no-op, registry-checked.
- **Naming:** raw filenames are `{YYYY-MM-DD}--{type}--{slug}.md`; slug is lowercased, non-alnum→`-`, collapsed, trimmed, ≤80 chars. Conversations live under `raw/conversations/`.
- **No model calls in M0:** converters are deterministic only. No embeddings, no LLM, no SQLite.
- **TDD:** every core function is test-first. CLI commands get smoke tests only.
- **Commit cadence:** one commit per task (or per step within a task where noted).

---

## File Map (locked decomposition)

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, `kg` console script, pytest config |
| `Makefile` | `make dev`/`build`/`test`/`install`/`uninstall` targets |
| `.gitignore` | Ignore `.kg/kg.db`, `dist/`, `__pycache__`, `.venv` |
| `src/kg/__init__.py` | `__version__` |
| `src/kg/__main__.py` | `python -m kg` → calls `cli.main.app()` |
| `src/kg/paths.py` | `KgPaths` dataclass: every `.kg/` path, `for_root`, `ensure()` |
| `src/kg/config.py` | Pydantic `Config` + nested models; `from_path`, `default`, `render_toml` |
| `src/kg/ontology.py` | Pydantic node/edge models, allowed-type sets, `build_ontology_schema`, `write_ontology` |
| `src/kg/frontmatter.py` | `RawFrontmatter` dataclass, `render`, `parse` |
| `src/kg/chunking.py` | `Chunk` dataclass, `slugify`, `chunk_markdown` |
| `src/kg/convert.py` | `ConvertedDoc`, `convert_source`, per-type converters |
| `src/kg/registry.py` | `RegistryEntry`, `Registry` (append/has/get/all/unextracted/mark) |
| `src/kg/cli/__init__.py` | re-export `app` |
| `src/kg/cli/main.py` | Typer `app`, `--version`, command registration |
| `src/kg/cli/init.py` | `kg init` command |
| `src/kg/cli/raw.py` | `kg raw add`, `kg raw list` commands |
| `src/kg/cli/status.py` | `kg status` command |
| `src/kg/cli/config_cmd.py` | `kg config get` command |
| `tests/conftest.py` | shared fixtures (`tmp_project`) |
| `tests/test_*.py` | one per core module + `tests/cli/test_*.py` smoke tests |

---

### Task 1: Package scaffold + `kg --version`

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.gitignore`, `src/kg/__init__.py`, `src/kg/__main__.py`, `src/kg/cli/__init__.py`, `src/kg/cli/main.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/cli/__init__.py`, `tests/cli/test_main.py`

**Interfaces:**
- Produces: `kg.__version__: str`, `kg.cli.main.app: typer.Typer`, console script `kg` → `kg.cli.main:app`. `KgError(Exception)` base in `src/kg/__init__.py` for later modules to subclass.

- [x] **Step 1: Write the failing test**

```python
# tests/cli/test_main.py
import subprocess
import sys
from typer.testing import CliRunner
from kg.cli.main import app

runner = CliRunner()

def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "kg" in result.stdout.lower()

def test_module_entrypoint_runs():
    # `python -m kg --version` must exit 0
    proc = subprocess.run(
        [sys.executable, "-m", "kg", "--version"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "kg" in proc.stdout.lower()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg'`

- [x] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "kg"
version = "0.0.1"
description = "Portable, local-first unified memory layer (files + vectors + graph) for any harness."
requires-python = ">=3.11"
readme = "README.md"
dependencies = [
    "typer>=0.12",
    "pydantic>=2.6",
    "tiktoken>=0.7",
    "pyyaml>=6.0",
    "markitdown>=0.0.1",
    "trafilatura>=1.12",
]

[project.scripts]
kg = "kg.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/kg"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[dependency-groups]
dev = ["pytest>=8", "pytest-cov>=5"]
```

- [x] **Step 4: Write `.gitignore`**

```
__pycache__/
*.pyc
.venv/
dist/
build/
*.egg-info/
.coverage
htmlcov/
.kg/kg.db
.kg/kg.db-*
```

- [x] **Step 5: Write package modules**

```python
# src/kg/__init__.py
__version__ = "0.0.1"


class KgError(Exception):
    """Base class for all kg errors."""
```

```python
# src/kg/__main__.py
from kg.cli.main import app

if __name__ == "__main__":
    app()
```

```python
# src/kg/cli/__init__.py
from kg.cli.main import app

__all__ = ["app"]
```

```python
# src/kg/cli/main.py
import typer
from kg import __version__

app = typer.Typer(
    name="kg",
    help="Portable, local-first unified memory layer.",
    no_args_is_help=True,
)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", help="Show kg version and exit.",
    ),
) -> None:
    if version:
        typer.echo(f"kg {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
```

```python
# tests/__init__.py
```
```python
# tests/cli/__init__.py
```
```python
# tests/conftest.py
import pytest


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """A throwaway cwd; tests cd into it via monkeypatch."""
    monkeypatch.chdir(tmp_path)
    return tmp_path
```

- [x] **Step 6: Write `Makefile`**

```makefile
PKG := kg
H ?= claude

.PHONY: dev build test install uninstall clean

dev:           ## editable install for working on kg itself
	uv sync --extra dev || uv sync
	uv pip install -e .

build:         ## build the wheel into dist/
	uv build

test:          ## run the test suite
	uv run pytest

install:       ## full bootstrap: build + global tool install + kg install + kg init
	uv sync && uv build && uv tool install ./dist/$(PKG)-*.whl --force && \
		kg install $(H) && kg init

uninstall:     ## reverse of install
	-kg install --uninstall $(H)
	-uv tool uninstall $(PKG)

clean:
	rm -rf dist build *.egg-info
```

- [x] **Step 7: Run test to verify it passes**

Run: `uv sync && uv run pytest tests/cli/test_main.py -v`
Expected: PASS (2 passed)

- [x] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(m0): package scaffold + kg --version CLI"
```

---

### Task 2: `KgPaths` — the `.kg/` layout in one place

**Files:**
- Create: `src/kg/paths.py`, `tests/test_paths.py`

**Interfaces:**
- Produces: `KgPaths(root: Path)` with attributes `raw`, `raw_conversations`, `wiki`, `wiki_index`, `registry`, `ontology`, `config`, `snapshots`, `review`, `kg_db`. Classmethods `for_root(root) -> KgPaths`, `for_cwd() -> KgPaths` (finds `.kg` in cwd or raises `KgError`). Method `ensure() -> None` creates all dirs.

- [x] **Step 1: Write the failing test**

```python
# tests/test_paths.py
import pytest
from kg import KgError
from kg.paths import KgPaths


def test_for_root_builds_all_paths(tmp_path):
    p = KgPaths.for_root(tmp_path / ".kg")
    assert p.raw == tmp_path / ".kg" / "raw"
    assert p.raw_conversations == tmp_path / ".kg" / "raw" / "conversations"
    assert p.wiki == tmp_path / ".kg" / "wiki"
    assert p.wiki_index == tmp_path / ".kg" / "wiki" / "index.md"
    assert p.registry == tmp_path / ".kg" / "registry.jsonl"
    assert p.ontology == tmp_path / ".kg" / "ontology.json"
    assert p.config == tmp_path / ".kg" / "config.toml"
    assert p.snapshots == tmp_path / ".kg" / "snapshots"
    assert p.review == tmp_path / ".kg" / "review"
    assert p.kg_db == tmp_path / ".kg" / "kg.db"


def test_ensure_creates_dirs(tmp_path):
    p = KgPaths.for_root(tmp_path / ".kg")
    p.ensure()
    assert p.raw.is_dir()
    assert p.raw_conversations.is_dir()
    assert p.wiki.is_dir()
    assert p.snapshots.is_dir()
    assert p.review.is_dir()


def test_for_cwd_raises_when_no_kg_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(KgError):
        KgPaths.for_cwd()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.paths'`

- [x] **Step 3: Write implementation**

```python
# src/kg/paths.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from kg import KgError


@dataclass(frozen=True)
class KgPaths:
    root: Path

    @classmethod
    def for_root(cls, root: Path) -> "KgPaths":
        return cls(root=Path(root))

    @classmethod
    def for_cwd(cls, cwd: Path | None = None) -> "KgPaths":
        here = Path(cwd) if cwd else Path.cwd()
        kg_dir = here / ".kg"
        if not kg_dir.is_dir():
            raise KgError(
                f"No .kg/ found in {here}. Run `kg init` first."
            )
        return cls.for_root(kg_dir)

    @property
    def raw(self) -> Path:            return self.root / "raw"
    @property
    def raw_conversations(self) -> Path: return self.root / "raw" / "conversations"
    @property
    def wiki(self) -> Path:           return self.root / "wiki"
    @property
    def wiki_index(self) -> Path:     return self.wiki / "index.md"
    @property
    def registry(self) -> Path:       return self.root / "registry.jsonl"
    @property
    def ontology(self) -> Path:       return self.root / "ontology.json"
    @property
    def config(self) -> Path:         return self.root / "config.toml"
    @property
    def snapshots(self) -> Path:      return self.root / "snapshots"
    @property
    def review(self) -> Path:         return self.root / "review"
    @property
    def kg_db(self) -> Path:          return self.root / "kg.db"

    def ensure(self) -> None:
        for d in (self.raw, self.raw_conversations, self.wiki,
                  self.snapshots, self.review):
            d.mkdir(parents=True, exist_ok=True)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS (3 passed)

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): KgPaths — single source of .kg/ layout"
```

---

### Task 3: `Config` — load/validate `.kg/config.toml`

**Files:**
- Create: `src/kg/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `Config` (pydantic) with nested `ProjectConfig`, `BackendConfig`, `EmbeddingConfig`, `ThresholdsConfig`, `ChunkingConfig`, `QueryConfig`, `DreamConfig`. `Config.default(user_id: str, scope: str) -> Config`. `Config.from_path(path: Path) -> Config`. `Config.render_toml() -> str`.

- [x] **Step 4 consumes this:** `kg init` writes `render_toml()` to `.kg/config.toml`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_config.py
from kg.config import Config


def test_default_has_spec_values():
    c = Config.default(user_id="quan", scope="my-agent")
    assert c.project.user_id == "quan"
    assert c.project.scope == "my-agent"
    assert c.backend.kind == "sqlite"
    assert c.backend.path == ".kg/kg.db"
    assert c.chunking.tokens == 512
    assert c.chunking.overlap == 64
    assert c.embedding.provider == "local"
    assert c.embedding.model == "bge-small-en-v1.5"
    assert c.thresholds.dedup_merge == 0.95
    assert c.thresholds.dedup_flag == 0.85
    assert c.thresholds.resolve_fuzzy == 0.85
    assert c.thresholds.resolve_semantic == 0.80
    assert c.query.rrf_k == 60
    assert c.query.pack_budget_tokens == 4000
    assert c.query.subgraph_cap == 300
    assert c.dream.auto_hook is False


def test_render_and_load_roundtrip(tmp_path):
    c = Config.default(user_id="quan", scope="my-agent")
    path = tmp_path / "config.toml"
    path.write_text(c.render_toml(), encoding="utf-8")
    loaded = Config.from_path(path)
    assert loaded.project.user_id == "quan"
    assert loaded.chunking.tokens == 512
    assert loaded.thresholds.dedup_weights.embedding == 0.7


def test_from_path_missing_file_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        Config.from_path(tmp_path / "nope.toml")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.config'`

- [x] **Step 3: Write implementation**

```python
# src/kg/config.py
from __future__ import annotations
import tomllib
from pathlib import Path
from pydantic import BaseModel, Field


class ProjectConfig(BaseModel):
    scope: str = "default"
    user_id: str = "user"


class BackendConfig(BaseModel):
    kind: str = "sqlite"
    path: str = ".kg/kg.db"


class EmbeddingConfig(BaseModel):
    provider: str = "local"
    model: str = "bge-small-en-v1.5"
    embed_fields: dict[str, list[str]] = Field(default_factory=dict)


class ThresholdsConfig(BaseModel):
    resolve_fuzzy: float = 0.85
    resolve_semantic: float = 0.80
    dedup_merge: float = 0.95
    dedup_flag: float = 0.85


class DedupWeights(BaseModel):
    embedding: float = 0.7
    fuzzy: float = 0.3


class ChunkingConfig(BaseModel):
    tokens: int = 512
    overlap: int = 64


class QueryConfig(BaseModel):
    rrf_k: int = 60
    default_hops: int = 2
    deep_search_hops: int = 3
    pack_budget_tokens: int = 4000
    subgraph_cap: int = 300


class DreamConfig(BaseModel):
    recent_window: str = "since-last-dream"
    auto_hook: bool = False


class Config(BaseModel):
    project: ProjectConfig = ProjectConfig()
    backend: BackendConfig = BackendConfig()
    embedding: EmbeddingConfig = EmbeddingConfig(
        embed_fields={
            "person": ["name", "summary", "attributes.role", "attributes.email"],
            "object": ["name", "summary", "attributes.model"],
        }
    )
    thresholds: ThresholdsConfig = ThresholdsConfig()
    chunking: ChunkingConfig = ChunkingConfig()
    query: QueryConfig = QueryConfig()
    dream: DreamConfig = DreamConfig()

    @classmethod
    def default(cls, user_id: str = "user", scope: str = "default") -> "Config":
        return cls(project=ProjectConfig(user_id=user_id, scope=scope))

    @classmethod
    def from_path(cls, path: Path) -> "Config":
        if not Path(path).exists():
            raise FileNotFoundError(path)
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return cls.model_validate(data)

    def render_toml(self) -> str:
        # Stable, human-readable TOML matching spec §12 layout.
        e = self.embedding
        t = self.thresholds
        q = self.query
        embed_fields_block = "\n".join(
            f'                   {k} = {v!r}' for k, v in e.embed_fields.items()
        )
        return f"""[project]
scope = "{self.project.scope}"
user_id = "{self.project.user_id}"

[backend]
kind = "{self.backend.kind}"
path = "{self.backend.path}"

[embedding]
provider = "{e.provider}"
model = "{e.model}"
[embedding.embed_fields]
{chr(10).join(f'{k} = {v!r}' for k, v in e.embed_fields.items()) or '# (none)'}

[thresholds]
resolve_fuzzy = {t.resolve_fuzzy}
resolve_semantic = {t.resolve_semantic}
dedup_merge = {t.dedup_merge}
dedup_flag = {t.dedup_flag}
[thresholds.dedup_weights]
embedding = 0.7
fuzzy = 0.3

[chunking]
tokens = {self.chunking.tokens}
overlap = {self.chunking.overlap}

[query]
rrf_k = {q.rrf_k}
default_hops = {q.default_hops}
deep_search_hops = {q.deep_search_hops}
pack_budget_tokens = {q.pack_budget_tokens}
subgraph_cap = {q.subgraph_cap}

[dream]
recent_window = "{self.dream.recent_window}"
auto_hook = {str(self.dream.auto_hook).lower()}
"""
```

> Note: `dedup_weights` is serialized as a fixed block because the weights are part of the contract; thresholds carry it as a sub-table for M1 to read. The test asserts `thresholds.dedup_weights.embedding == 0.7`, so add `dedup_weights: DedupWeights = DedupWeights()` to `ThresholdsConfig` before running. (Apply that one-line addition in Step 3.)

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (3 passed)

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): Config pydantic model + TOML render/load"
```

---

### Task 4: `ontology.py` — the contract artifact

**Files:**
- Create: `src/kg/ontology.py`, `tests/test_ontology.py`

**Interfaces:**
- Produces: `ALLOWED_NODE_TYPES: set[str]`, `ALLOWED_SEMANTIC_EDGE_TYPES: set[str]`, `STRUCTURAL_EDGE_TYPES: set[str]`, `Node` and `Edge` pydantic models, `build_ontology_schema() -> dict`, `write_ontology(path: Path, ontology_version: int = 1) -> None`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_ontology.py
import json
from kg.ontology import (
    ALLOWED_NODE_TYPES, ALLOWED_SEMANTIC_EDGE_TYPES,
    build_ontology_schema, write_ontology,
)


def test_pole_types_present():
    for t in ("person", "organization", "location", "event", "object",
              "preference", "fact", "document", "chunk", "conversation", "session"):
        assert t in ALLOWED_NODE_TYPES


def test_semantic_edges_present():
    for e in ("employed_by", "member_of", "knows", "located_at", "alias_of",
              "uses", "owns", "part_of", "next", "mentions",
              "same_as", "superseded_by"):
        assert e in ALLOWED_SEMANTIC_EDGE_TYPES


def test_write_ontology_creates_valid_json(tmp_path):
    out = tmp_path / "ontology.json"
    write_ontology(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ontology_version"] == 1
    assert data["node_types"]
    assert data["edge_types"]


def test_build_schema_includes_node_model():
    schema = build_ontology_schema()
    assert "Node" in schema["$defs"] or "properties" in schema
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ontology.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.ontology'`

- [x] **Step 3: Write implementation**

```python
# src/kg/ontology.py
from __future__ import annotations
import json
from pathlib import Path
from pydantic import BaseModel, Field


ALLOWED_NODE_TYPES: set[str] = {
    "person", "organization", "location", "event", "object",
    "preference", "fact",
    "document", "chunk",
    "conversation", "session",
}

ALLOWED_SEMANTIC_EDGE_TYPES: set[str] = {
    "employed_by", "member_of", "knows", "located_at", "resides_at",
    "alias_of", "has_task", "uses", "owns", "related_to",
}

STRUCTURAL_EDGE_TYPES: set[str] = {
    "part_of", "next", "mentions", "same_as", "superseded_by",
}


class Node(BaseModel):
    id: str | None = None
    type: str
    subtype: str | None = None
    name: str
    canonical_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    summary: str | None = None
    attributes: dict = Field(default_factory=dict)
    valid_from: str | None = None
    valid_until: str | None = None
    sources: list[dict] = Field(default_factory=list)
    status: str = "active"


class Edge(BaseModel):
    id: str | None = None
    type: str = "related_to"
    semantic_type: str
    summary: str | None = None
    confidence: float = 0.0
    sources: list[dict] = Field(default_factory=list)
    valid_from: str | None = None
    valid_until: str | None = None


def build_ontology_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "node_types": sorted(ALLOWED_NODE_TYPES),
        "edge_types": {
            "semantic": sorted(ALLOWED_SEMANTIC_EDGE_TYPES),
            "structural": sorted(STRUCTURAL_EDGE_TYPES),
        },
        "$defs": {
            "Node": Node.model_json_schema(),
            "Edge": Edge.model_json_schema(),
        },
    }


def write_ontology(path: Path, ontology_version: int = 1) -> None:
    schema = build_ontology_schema()
    schema["ontology_version"] = ontology_version
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ontology.py -v`
Expected: PASS (4 passed)

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): ontology contract — node/edge models + JSON schema writer"
```

---

### Task 5: `slugify` + `chunking` (heading-aware, tiktoken)

**Files:**
- Create: `src/kg/chunking.py`, `tests/test_chunking.py`

**Interfaces:**
- Produces: `slugify(text: str, max_len: int = 80) -> str`, `Chunk(index, start_char, end_char, text, token_count)`, `chunk_markdown(text: str, tokens: int = 512, overlap: int = 64) -> list[Chunk]`.
- `chunk_markdown` consumes: `tiktoken.get_encoding("cl100k")`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_chunking.py
from kg.chunking import slugify, chunk_markdown, Chunk


def test_slugify_basic():
    assert slugify("Keep Your Knowledge Graph Clean!") == "keep-your-knowledge-graph-clean"
    assert slugify("  Paris, France  ") == "paris-france"
    assert slugify("a/b:c") == "a-b-c"


def test_slugify_cap():
    assert len(slugify("x" * 200)) == 80


def test_chunk_markdown_short_text_is_one_chunk():
    chunks = chunk_markdown("hello world", tokens=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0].text == "hello world"
    assert chunks[0].index == 0
    assert chunks[0].token_count > 0
    assert chunks[0].start_char == 0


def test_chunk_markdown_respects_heading_boundaries():
    body = "# A\n\n" + ("alpha. " * 400) + "\n\n# B\n\n" + ("bravo. " * 400)
    chunks = chunk_markdown(body, tokens=64, overlap=8)
    # More than one chunk; at least one chunk begins at a heading.
    assert len(chunks) > 1
    starts = {body[c.start_char:c.start_char + 2] for c in chunks}
    assert any(s in ("# ",) for s in starts)


def test_chunks_cover_full_text():
    body = "word. " * 1000
    chunks = chunk_markdown(body, tokens=32, overlap=8)
    assert chunks[-1].end_char == len(body)
    assert all(c.end_char > c.start_char for c in chunks)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_chunking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.chunking'`

- [x] **Step 3: Write implementation**

```python
# src/kg/chunking.py
from __future__ import annotations
import re
from dataclasses import dataclass
import tiktoken

_ENC = tiktoken.get_encoding("cl100k")
_HEADING = re.compile(r"^(#{1,6})\s+", re.MULTILINE)


def slugify(text: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len]


@dataclass(frozen=True)
class Chunk:
    index: int
    start_char: int
    end_char: int
    text: str
    token_count: int


def _heading_offsets(text: str) -> list[int]:
    return [m.start() for m in _HEADING.finditer(text)] or [0]


def _split_to_segments(text: str) -> list[tuple[int, int]]:
    """Return (start_char, end_char) segments that begin at each heading."""
    offs = _heading_offsets(text)
    if 0 not in offs:
        offs = [0, *offs]
    segs = []
    for i, start in enumerate(offs):
        end = offs[i + 1] if i + 1 < len(offs) else len(text)
        segs.append((start, end))
    return segs


def _fill(seg_text: str, tokens: int, overlap: int):
    """Greedy token-budget fill of one segment's text -> list[(start,end)] char spans."""
    ids = _ENC.encode(seg_text)
    if len(ids) <= tokens:
        return [(0, len(seg_text))]
    spans = []
    step = max(1, tokens - overlap)
    i = 0
    while i < len(ids):
        window = ids[i:i + tokens]
        a = _ENC.decode_window(ids, i) if False else None  # placeholder; replaced below
        start_char = len(_ENC.decode(ids[:i]))
        end_char = len(_ENC.decode(ids[: i + len(window)]))
        spans.append((start_char, end_char))
        if i + len(window) >= len(ids):
            break
        i += step
    return spans


def chunk_markdown(text: str, tokens: int = 512, overlap: int = 64) -> list[Chunk]:
    if not text.strip():
        return []
    chunks: list[Chunk] = []
    idx = 0
    for seg_start, seg_end in _split_to_segments(text):
        seg_text = text[seg_start:seg_end]
        for (a, b) in _fill(seg_text, tokens=tokens, overlap=overlap):
            chunk_text = seg_text[a:b]
            if not chunk_text.strip():
                continue
            chunks.append(Chunk(
                index=idx,
                start_char=seg_start + a,
                end_char=seg_start + b,
                text=chunk_text,
                token_count=len(_ENC.encode(chunk_text)),
            ))
            idx += 1
    return chunks
```

> Replace the placeholder line in `_fill` (the `a = …` line is dead code) — keep only `start_char`/`end_char` lines. The test asserts coverage and heading-boundary behavior, not exact count.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_chunking.py -v`
Expected: PASS (5 passed). If `test_chunks_cover_full_text` fails by a tail character, clamp the last chunk's `end_char` to `len(text)` in `chunk_markdown` before committing.

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): slugify + heading-aware chunking (tiktoken cl100k)"
```

---

### Task 6: `frontmatter.py` — YAML frontmatter for raw files

**Files:**
- Create: `src/kg/frontmatter.py`, `tests/test_frontmatter.py`

**Interfaces:**
- Produces: `RawFrontmatter` dataclass (`source: str`, `sha256: str`, `type: str`, `title: str`, `ingested_at: str`, `chunks: list[dict]` where each dict is `{index, start_char, end_char, token_count}`). Functions `render(fm: RawFrontmatter, body: str) -> str` and `parse(text: str) -> tuple[RawFrontmatter, str]`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_frontmatter.py
from kg.frontmatter import RawFrontmatter, render, parse


def test_render_parse_roundtrip():
    fm = RawFrontmatter(
        source="/abs/paper.pdf", sha256="abc123", type="pdf",
        title="My Paper", ingested_at="2026-07-26T12:00:00Z",
        chunks=[{"index": 0, "start_char": 0, "end_char": 10, "token_count": 3}],
    )
    doc = render(fm, "body text")
    assert doc.startswith("---\n")
    fm2, body = parse(doc)
    assert fm2.sha256 == "abc123"
    assert fm2.title == "My Paper"
    assert fm2.chunks[0]["token_count"] == 3
    assert body == "body text"


def test_parse_rejects_missing_frontmatter():
    import pytest
    with pytest.raises(ValueError):
        parse("no frontmatter here")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_frontmatter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write implementation**

```python
# src/kg/frontmatter.py
from __future__ import annotations
import re
from dataclasses import dataclass, asdict
import yaml

_FM = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class RawFrontmatter:
    source: str
    sha256: str
    type: str
    title: str
    ingested_at: str
    chunks: list[dict]


def render(fm: RawFrontmatter, body: str) -> str:
    payload = asdict(fm)
    yaml_block = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{yaml_block}\n---\n{body}"


def parse(text: str) -> tuple[RawFrontmatter, str]:
    m = _FM.match(text)
    if not m:
        raise ValueError("Missing YAML frontmatter block.")
    data = yaml.safe_load(m.group(1))
    fm = RawFrontmatter(
        source=data["source"], sha256=data["sha256"], type=data["type"],
        title=data.get("title", ""), ingested_at=data["ingested_at"],
        chunks=data.get("chunks", []),
    )
    return fm, m.group(2)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_frontmatter.py -v`
Expected: PASS (2 passed)

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): YAML frontmatter render/parse for raw files"
```

---

### Task 7: `convert.py` — source → markdown

**Files:**
- Create: `src/kg/convert.py`, `tests/test_convert.py`

**Interfaces:**
- Produces: `ConvertedDoc(markdown: str, title: str | None)`, `convert_source(source: str, type: str | None, title: str | None) -> ConvertedDoc`. `source` is a filesystem path, a URL (http/https), or `"-"` for stdin. `type` is one of `pdf|docx|md|markdown|html|url|text` (auto-detected from `source` when `None`).
- Consumes: markitdown (pdf/docx/html), trafilatura (url), stdin via `sys.stdin`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_convert.py
import pytest
from kg.convert import convert_source, ConvertedDoc


def test_text_passthrough():
    doc = convert_source("hello world", type="text", title=None)
    assert isinstance(doc, ConvertedDoc)
    assert doc.markdown.strip() == "hello world"
    assert doc.title is None


def test_markdown_passthrough(tmp_path):
    f = tmp_path / "n.md"
    f.write_text("# Title\n\nbody")
    doc = convert_source(str(f), type=None, title=None)
    assert "# Title" in doc.markdown


def test_html_via_markitdown(tmp_path):
    f = tmp_path / "p.html"
    f.write_text("<html><body><h1>Hi</h1><p>There</p></body></html>")
    doc = convert_source(str(f), type=None, title=None)
    assert "There" in doc.markdown


def test_unknown_type_raises(tmp_path):
    with pytest.raises(ValueError):
        convert_source("x", type="bogus", title=None)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        convert_source(str(tmp_path / "nope.pdf"), type=None, title=None)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_convert.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write implementation**

```python
# src/kg/convert.py
from __future__ import annotations
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class ConvertedDoc:
    markdown: str
    title: str | None


def _detect_type(source: str) -> str:
    if source == "-":
        return "text"
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return "url"
    suffix = Path(source).suffix.lower().lstrip(".")
    return {"md": "md", "markdown": "md", "html": "html", "htm": "html",
            "pdf": "pdf", "docx": "docx"}.get(suffix, "text")


def _convert_markitdown(path: str) -> str:
    from markitdown import MarkItDown
    return MarkItDown().convert(path).text_content


def _convert_url(url: str) -> str:
    import trafilatura
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError(f"Could not fetch URL: {url}")
    return trafilatura.extract(downloaded) or ""


def convert_source(source: str, type: str | None, title: str | None) -> ConvertedDoc:
    kind = type or _detect_type(source)
    if kind == "text":
        md = source if source != "-" else sys.stdin.read()
    elif kind in ("md", "markdown"):
        md = Path(source).read_text(encoding="utf-8")
    elif kind == "url":
        md = _convert_url(source)
    elif kind in ("html", "pdf", "docx"):
        if not Path(source).exists():
            raise FileNotFoundError(source)
        md = _convert_markitdown(source)
    else:
        raise ValueError(f"Unsupported source type: {kind!r}")
    return ConvertedDoc(markdown=md, title=title)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_convert.py -v`
Expected: PASS (5 passed). (markitdown + trafilatura pulled in by `uv sync`.)

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): source converters (text/md/html/pdf/docx/url)"
```

---

### Task 8: `registry.py` — append-only registry, dedupe by sha256

**Files:**
- Create: `src/kg/registry.py`, `tests/test_registry.py`

**Interfaces:**
- Produces: `RegistryEntry` dataclass (`sha256, path, source, type, title, ingested_at, extracted=False, chunks_done=[], chunks_failed={}`). `Registry(path: Path)` with `append(entry) -> bool` (False if sha256 already present), `has(sha256) -> bool`, `get(sha256) -> RegistryEntry | None`, `all() -> list[RegistryEntry]`, `unextracted() -> list[RegistryEntry]`, `mark_extracted(sha256, chunks_done, chunks_failed) -> None`.
- Consumes: `RawFrontmatter` shape (not imported; registry is plain data).

- [x] **Step 1: Write the failing test**

```python
# tests/test_registry.py
import pytest
from kg.registry import Registry, RegistryEntry


def _entry(sha="a", path="raw/x.md"):
    return RegistryEntry(sha256=sha, path=path, source="/src",
                         type="text", title="t", ingested_at="2026-07-26T00:00:00Z")


def test_append_and_has(tmp_path):
    reg = Registry(tmp_path / "registry.jsonl")
    assert reg.append(_entry()) is True
    assert reg.has("a") is True
    assert reg.append(_entry()) is False  # idempotent / dedupe


def test_get_and_all(tmp_path):
    reg = Registry(tmp_path / "registry.jsonl")
    reg.append(_entry("a", "raw/a.md"))
    reg.append(_entry("b", "raw/b.md"))
    assert reg.get("b").path == "raw/b.md"
    assert len(reg.all()) == 2


def test_unextracted_and_mark(tmp_path):
    reg = Registry(tmp_path / "registry.jsonl")
    reg.append(_entry("a"))
    reg.append(_entry("b"))
    assert len(reg.unextracted()) == 2
    reg.mark_extracted("a", chunks_done=[0, 1], chunks_failed={})
    assert len(reg.unextracted()) == 1
    assert reg.get("a").extracted is True
    assert reg.get("a").chunks_done == [0, 1]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write implementation**

```python
# src/kg/registry.py
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class RegistryEntry:
    sha256: str
    path: str
    source: str
    type: str
    title: str
    ingested_at: str
    extracted: bool = False
    chunks_done: list[int] = field(default_factory=list)
    chunks_failed: dict = field(default_factory=dict)


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _iter_lines(self):
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)

    def has(self, sha256: str) -> bool:
        return any(e["sha256"] == sha256 for e in self._iter_lines())

    def get(self, sha256: str) -> RegistryEntry | None:
        for e in self._iter_lines():
            if e["sha256"] == sha256:
                return RegistryEntry(**e)
        return None

    def all(self) -> list[RegistryEntry]:
        return [RegistryEntry(**e) for e in self._iter_lines()]

    def unextracted(self) -> list[RegistryEntry]:
        return [e for e in self.all() if not e.extracted]

    def append(self, entry: RegistryEntry) -> bool:
        if self.has(entry.sha256):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")
        return True

    def mark_extracted(self, sha256: str,
                       chunks_done: list[int], chunks_failed: dict) -> None:
        entries = self.all()
        for e in entries:
            if e.sha256 == sha256:
                e.extracted = True
                e.chunks_done = chunks_done
                e.chunks_failed = chunks_failed
        self.path.write_text(
            "".join(json.dumps(asdict(e)) + "\n" for e in entries),
            encoding="utf-8",
        )
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_registry.py -v`
Expected: PASS (3 passed)

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): registry.jsonl append/dedupe/checkpoint state"
```

---

### Task 9: `kg init` command

**Files:**
- Create: `src/kg/cli/init.py`, `tests/cli/test_init.py`
- Modify: `src/kg/cli/main.py` (register the `init` subcommand)

**Interfaces:**
- Produces: `init_project(cwd: Path, user_id: str, scope: str) -> KgPaths` (core function, called by CLI and tests). Idempotent: re-running in an existing `.kg/` does not clobber config/ontology but ensures dirs + index exist.

- [x] **Step 1: Write the failing test**

```python
# tests/cli/test_init.py
from kg.cli.init import init_project
from kg.paths import KgPaths


def test_init_creates_full_layout(tmp_path):
    p = init_project(tmp_path, user_id="quan", scope="my-agent")
    assert (tmp_path / ".kg").is_dir()
    assert p.config.is_file()
    assert p.ontology.is_file()
    assert p.registry.is_file()
    assert p.wiki_index.is_file()
    assert p.raw.is_dir()
    assert "quan" in p.config.read_text()


def test_init_is_idempotent(tmp_path):
    init_project(tmp_path, user_id="a", scope="s")
    cfg_before = (tmp_path / ".kg" / "config.toml").read_text()
    init_project(tmp_path, user_id="a", scope="s")  # must not crash
    cfg_after = (tmp_path / ".kg" / "config.toml").read_text()
    assert cfg_before == cfg_after
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_init.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write implementation**

```python
# src/kg/cli/init.py
from __future__ import annotations
from pathlib import Path
from kg.paths import KgPaths
from kg.config import Config
from kg.ontology import write_ontology


def init_project(cwd: Path, user_id: str, scope: str) -> KgPaths:
    paths = KgPaths.for_root(cwd / ".kg")
    paths.ensure()

    if not paths.config.exists():
        cfg = Config.default(user_id=user_id, scope=scope)
        paths.config.write_text(cfg.render_toml(), encoding="utf-8")

    if not paths.ontology.exists():
        write_ontology(paths.ontology)

    paths.registry.touch(exist_ok=True)

    if not paths.wiki_index.exists():
        paths.wiki_index.write_text(
            f"# {scope} — Memory Index\n\n_Sources ingested into kg._\n\n",
            encoding="utf-8",
        )
    return paths
```

Register in `main.py` — add after the callback:

```python
# src/kg/cli/main.py  (add at bottom, after _root)
from kg.cli import init as init_cmd  # noqa: E402

app.command(name="init")(init_cmd.init_cli)
```

And the CLI wrapper:

```python
# src/kg/cli/init.py  (append)
import typer

def init_cli(
    user_id: str = typer.Option("user", "--user-id", help="Owner id embedded in node IDs."),
    scope: str = typer.Option("default", "--scope", help="Per-project scope label."),
) -> None:
    from pathlib import Path
    paths = init_project(Path.cwd(), user_id=user_id, scope=scope)
    typer.echo(f"Initialized kg memory at {paths.root}")
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_init.py -v`
Expected: PASS (2 passed)

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): kg init — creates .kg/ layout, config, ontology, index"
```

---

### Task 10: `kg raw add` + `kg raw list`

**Files:**
- Create: `src/kg/raw_ops.py`, `src/kg/cli/raw.py`, `tests/test_raw_ops.py`, `tests/cli/test_raw.py`
- Modify: `src/kg/cli/main.py` (register `raw` subgroup)

**Interfaces:**
- Produces (core, in `raw_ops.py`): `add_source(paths: KgPaths, config: Config, source: str, type: str | None, title: str | None, conversation: bool = False) -> tuple[bool, str]` returning `(added, raw_relpath)` where `added=False` means deduped. `list_sources(paths: KgPaths, unextracted_only: bool) -> list[RegistryEntry]`.
- Consumes: `KgPaths`, `Config`, `convert_source`, `chunk_markdown`, `RawFrontmatter`, `Registry`, `RegistryEntry`, `slugify`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_raw_ops.py
import hashlib
from kg.cli.init import init_project
from kg.config import Config
from kg.raw_ops import add_source, list_sources


def _setup(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="s")
    cfg = Config.default(user_id="u", scope="s")
    return paths, cfg


def test_add_text_source_creates_raw_and_registry(tmp_path):
    paths, cfg = _setup(tmp_path)
    added, rel = add_source(paths, cfg, "hello world", type="text", title="Hi")
    assert added is True
    raw_file = paths.root / rel
    assert raw_file.is_file()
    fm, body = _parse(raw_file.read_text(encoding="utf-8"))
    assert body.strip() == "hello world"
    assert fm.type == "text"
    assert len(fm.chunks) >= 1
    assert len(list_sources(paths, unextracted_only=False)) == 1


def test_re_add_same_content_is_noop(tmp_path):
    paths, cfg = _setup(tmp_path)
    add_source(paths, cfg, "hello world", type="text", title="Hi")
    added2, _ = add_source(paths, cfg, "hello world", type="text", title="Hi")
    assert added2 is False
    assert len(list_sources(paths, unextracted_only=False)) == 1


def test_unextracted_filter(tmp_path):
    paths, cfg = _setup(tmp_path)
    add_source(paths, cfg, "aaa", type="text", title=None)
    from kg.registry import Registry
    Registry(paths.registry).mark_extracted(
        hashlib.sha256(b"aaa").hexdigest(), [], {})
    assert len(list_sources(paths, unextracted_only=True)) == 0


def _parse(text):
    from kg.frontmatter import parse
    return parse(text)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_raw_ops.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write implementation**

```python
# src/kg/raw_ops.py
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from kg.config import Config
from kg.paths import KgPaths
from kg.convert import convert_source
from kg.chunking import chunk_markdown, slugify
from kg.frontmatter import RawFrontmatter, render
from kg.registry import Registry, RegistryEntry


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _raw_relpath(type_: str, title: str | None, sha: str,
                 conversation: bool) -> str:
    from urllib.parse import urlparse
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = title or Path(sha[:12]).stem
    name = f"{today}--{type_}--{slugify(base)}.md"
    sub = "conversations" if conversation else ""
    return f"raw/{sub}/{name}" if sub else f"raw/{name}"


def add_source(paths: KgPaths, config: Config, source: str,
               type: str | None, title: str | None,
               conversation: bool = False) -> tuple[bool, str]:
    doc = convert_source(source, type=type, title=title)
    sha = _sha256(doc.markdown)
    reg = Registry(paths.registry)
    if reg.has(sha):
        existing = reg.get(sha)
        return False, existing.path

    chunks = chunk_markdown(
        doc.markdown,
        tokens=config.chunking.tokens,
        overlap=config.chunking.overlap,
    )
    chunk_dicts = [
        {"index": c.index, "start_char": c.start_char,
         "end_char": c.end_char, "token_count": c.token_count}
        for c in chunks
    ]
    fm = RawFrontmatter(
        source=source, sha256=sha, type=type or "text",
        title=title or "", ingested_at=datetime.now(timezone.utc).isoformat(),
        chunks=chunk_dicts,
    )
    rel = _raw_relpath(fm.type, title, sha, conversation)
    out = paths.root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(fm, doc.markdown), encoding="utf-8")

    reg.append(RegistryEntry(
        sha256=sha, path=rel, source=source, type=fm.type,
        title=title or "", ingested_at=fm.ingested_at,
    ))
    return True, rel


def list_sources(paths: KgPaths, unextracted_only: bool) -> list[RegistryEntry]:
    reg = Registry(paths.registry)
    return reg.unextracted() if unextracted_only else reg.all()
```

CLI wrapper:

```python
# src/kg/cli/raw.py
from __future__ import annotations
import typer
from kg.paths import KgPaths
from kg.config import Config
from kg.raw_ops import add_source, list_sources

raw_app = typer.Typer(help="Manage raw sources under .kg/raw/.")


@raw_app.command("add")
def raw_add(
    source: str = typer.Argument(..., help="Path, URL, or '-' for stdin."),
    type: str = typer.Option(None, "--type", help="pdf|docx|md|html|url|text"),
    title: str = typer.Option(None, "--title"),
    conversation: bool = typer.Option(False, "--conversation"),
) -> None:
    paths = KgPaths.for_cwd()
    cfg = Config.from_path(paths.config)
    added, rel = add_source(paths, cfg, source, type, title, conversation)
    if added:
        typer.echo(f"added: {rel}")
        typer.echo("next: run /kg:extract")
    else:
        typer.echo(f"skipped (duplicate): {rel}")


@raw_app.command("list")
def raw_list(
    unextracted: bool = typer.Option(False, "--unextracted"),
) -> None:
    paths = KgPaths.for_cwd()
    for e in list_sources(paths, unextracted_only=unextracted):
        flag = "" if e.extracted else " [unextracted]"
        typer.echo(f"{e.path}\t{e.type}\t{e.title}{flag}")
```

Register in `main.py`:

```python
# src/kg/cli/main.py  (append)
from kg.cli import raw as raw_cmd  # noqa: E402
app.add_typer(raw_cmd.raw_app, name="raw")
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_raw_ops.py tests/cli/test_init.py -v`
Expected: PASS

- [x] **Step 5: CLI smoke test**

```bash
cd /tmp && rm -rf kg-smoke && mkdir kg-smoke && cd kg-smoke
uv run --project <repo> kg init --user-id quan --scope smoke
echo "Demis Hassabis founded DeepMind." | uv run --project <repo> kg raw add - --type text --title "fact"
uv run --project <repo> kg raw list
uv run --project <repo> kg raw add - --type text --title "fact"  # re-add → duplicate
```
Expected: first add prints `added:`, list shows one source, second add prints `skipped (duplicate)`.

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(m0): kg raw add/list — convert→frontmatter→chunk→register"
```

---

### Task 11: `kg status` + `kg config get`

**Files:**
- Create: `src/kg/cli/status.py`, `src/kg/cli/config_cmd.py`, `tests/cli/test_status.py`, `tests/cli/test_config_cmd.py`
- Modify: `src/kg/cli/main.py` (register `status`, `config`)

**Interfaces:**
- Produces: `status_report(paths) -> dict` (core logic testable directly) returning `{sources, unextracted, extracted, last_ingested}`. `config_get(paths, key) -> str`.

- [x] **Step 1: Write the failing test**

```python
# tests/cli/test_status.py
from kg.cli.init import init_project
from kg.cli.status import status_report
from kg.raw_ops import add_source
from kg.config import Config


def test_status_counts(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="s")
    cfg = Config.default(user_id="u", scope="s")
    add_source(paths, cfg, "one", type="text", title=None)
    add_source(paths, cfg, "two", type="text", title=None)
    rep = status_report(paths)
    assert rep["sources"] == 2
    assert rep["unextracted"] == 2
    assert rep["extracted"] == 0
```

```python
# tests/cli/test_config_cmd.py
from kg.cli.init import init_project
from kg.cli.config_cmd import config_get


def test_config_get(tmp_path):
    init_project(tmp_path, user_id="quan", scope="s")
    assert config_get(tmp_path / ".kg", "project.user_id") == "quan"
    assert config_get(tmp_path / ".kg", "chunking.tokens") == "512"


def test_config_get_missing_key(tmp_path):
    import pytest
    init_project(tmp_path, user_id="u", scope="s")
    with pytest.raises(KeyError):
        config_get(tmp_path / ".kg", "nope.nope")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_status.py tests/cli/test_config_cmd.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write implementation**

```python
# src/kg/cli/status.py
from __future__ import annotations
from kg.paths import KgPaths
from kg.registry import Registry


def status_report(paths: KgPaths) -> dict:
    entries = Registry(paths.registry).all()
    extracted = [e for e in entries if e.extracted]
    return {
        "sources": len(entries),
        "unextracted": len(entries) - len(extracted),
        "extracted": len(extracted),
        "last_ingested": max((e.ingested_at for e in entries), default=None),
    }


import typer


def status_cli() -> None:
    paths = KgPaths.for_cwd()
    rep = status_report(paths)
    typer.echo(f"sources:    {rep['sources']}")
    typer.echo(f"extracted:  {rep['extracted']}")
    typer.echo(f"unextracted:{rep['unextracted']}")
    typer.echo(f"last ingest:{rep['last_ingested'] or '-'}")
```

```python
# src/kg/cli/config_cmd.py
from __future__ import annotations
from pathlib import Path
import typer
from kg.config import Config


def _walk(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if not hasattr(cur, part):
            raise KeyError(dotted)
        cur = getattr(cur, part)
    return cur


def config_get(kg_root: Path, key: str):
    cfg = Config.from_path(kg_root / "config.toml")
    return _walk(cfg, key)


def config_cli(key: str = typer.Argument(...)) -> None:
    from kg.paths import KgPaths
    val = config_get(KgPaths.for_cwd().root, key)
    typer.echo(val)
```

Register both in `main.py`:

```python
# src/kg/cli/main.py  (append)
from kg.cli import status as status_cmd   # noqa: E402
from kg.cli import config_cmd              # noqa: E402
app.command(name="status")(status_cmd.status_cli)
config_app = typer.Typer(help="Read kg config.")
app.add_typer(config_app, name="config")
config_app.command(name="get")(config_cmd.config_cli)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_status.py tests/cli/test_config_cmd.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m0): kg status + kg config get"
```

---

### Task 12: M0 acceptance — full smoke + docs stub

**Files:**
- Create: `README.md`, `tests/test_acceptance_m0.py`

**Interfaces:** none new — this task wires the spec §20 walkthrough's M0 portion into a single acceptance test and writes a minimal README.

- [x] **Step 1: Write the acceptance test**

```python
# tests/test_acceptance_m0.py
"""End-to-end M0: init → add 3 sources → dedupe → list → status.
Mirrors spec §20 (files-layer portion)."""
from typer.testing import CliRunner
from kg.cli.main import app


def test_m0_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()

    assert r.invoke(app, ["init", "--user-id", "quan", "--scope", "demo"]).exit_code == 0
    assert (tmp_path / ".kg" / "config.toml").is_file()
    assert (tmp_path / ".kg" / "ontology.json").is_file()
    assert (tmp_path / ".kg" / "registry.jsonl").is_file()

    # three sources
    (tmp_path / "a.md").write_text("# Alpha\n\nDeepMind makes AlphaFold.")
    (tmp_path / "b.md").write_text("# Bravo\n\nDemis Hassabis founded DeepMind.")
    assert r.invoke(app, ["raw", "add", str(tmp_path / "a.md")]).exit_code == 0
    assert r.invoke(app, ["raw", "add", str(tmp_path / "b.md")]).exit_code == 0

    # duplicate content → skipped
    dup = r.invoke(app, ["raw", "add", str(tmp_path / "a.md")])
    assert dup.exit_code == 0
    assert "skipped" in dup.stdout.lower() or "duplicate" in dup.stdout.lower()

    assert "2" in r.invoke(app, ["status"]).stdout
    listed = r.invoke(app, ["raw", "list"]).stdout
    assert "alpha" in listed.lower() or "bravo" in listed.lower()

    # chunk boundaries persisted in frontmatter
    from kg.registry import Registry
    from kg.paths import KgPaths
    from kg.frontmatter import parse
    e = Registry(KgPaths.for_cwd().registry).all()[0]
    fm, _ = parse((tmp_path / ".kg" / e.path).read_text(encoding="utf-8"))
    assert fm.chunks and fm.sha256


def test_m0_config_get_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init", "--user-id", "q", "--scope", "s"])
    out = CliRunner().invoke(app, ["config", "get", "chunking.tokens"])
    assert "512" in out.stdout
```

- [x] **Step 2: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests PASS (core + cli + acceptance).

- [x] **Step 3: Write `README.md`**

```markdown
# kg

Portable, local-first unified memory layer (files + vectors + graph) for any
harness (Claude Code, Codex, OpenCode, Cursor). The harness LLM does extraction;
the engine does deterministic storage/matching/search.

> **Status:** M0 (files layer) — `init`, `raw add/list`, `status`, `config`.
> Graph + normalization gate, dream, MCP, installers, viz land in M1–M6.

## Quick start (from this checkout)

```bash
make dev          # editable install for hacking on kg
kg init --user-id $USER --scope my-project
echo "some fact" | kg raw add - --type text --title note
kg raw list
kg status
```

## Build / install / uninstall

```bash
make build        # wheel into dist/
make install      # full bootstrap (M5 wiring) — default harness: claude
make install H=cursor
make uninstall
make test
```

## Layout

See `docs/superpowers/specs/2026-07-26-kg-unified-memory-design.md` for the full
design; `docs/superpowers/plans/` for milestone plans.
```

- [x] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(m0): acceptance test + README; M0 complete"
```

---

## Self-Review

**1. Spec coverage (M0 scope only — spec §3, §6.1, §12, §15.2, §19 M0):**
- `.kg/` storage layout (§3) → Task 2 (`KgPaths`), Task 9 (`init` creates it). ✓
- raw/ immutable + sha256 dedupe + frontmatter `{source, sha256, type, title, ingested_at, chunks}` (§3, §6.1) → Tasks 6, 8, 10. ✓
- chunking 512/64 tiktoken cl100k, heading-first (§6.1) → Task 5. ✓
- converters pdf/docx/html/markdown + trafilatura for URLs (§6.1) → Task 7. ✓
- registry.jsonl one-line-per-source + checkpoint fields (`chunks_done`, `chunks_failed`) (§6.2) → Task 8. ✓
- config.toml with spec §12 values → Task 3, verified by test. ✓
- ontology.json generated from Pydantic (§5) → Task 4. ✓
- `make dev/build/install/uninstall/test` targets (§15.2) → Task 1. ✓
- wiki/index.md seeded (§3) → Task 9. ✓
- CLI commands `init/raw add/raw list/status/config get` (§11, M0 subset) → Tasks 9, 10, 11. ✓
- git policy (ignore kg.db, commit raw/wiki/registry/ontology/config) → Task 1 `.gitignore`. ✓
- Gap check: `raw list --unextracted` flag → Task 10 `--unextracted`. ✓

**2. Placeholder scan:** Task 5 Step 3 has one dead placeholder line (`a = … if False else None`) explicitly flagged for removal before commit. No other TODO/TBD/"add error handling" remains. All test code and impl code are concrete.

**3. Type consistency:**
- `KgPaths.for_root` / `for_cwd` — used identically in Tasks 9, 10, 11. ✓
- `Config.default(user_id, scope)` / `Config.from_path(path)` — same signatures Tasks 3, 9, 10, 11. ✓
- `RegistryEntry` fields match across Tasks 8, 10 (`sha256, path, source, type, title, ingested_at, extracted, chunks_done, chunks_failed`). ✓
- `add_source(paths, config, source, type, title, conversation=False) -> tuple[bool, str]` — Task 10 defines, Task 12 + tests consume identically. ✓
- `status_report(paths) -> dict` keys `sources/unextracted/extracted/last_ingested` — Task 11 defines, test asserts. ✓
- `config_get(kg_root, key)` — Task 11 test passes `tmp_path / ".kg"`; impl reads `kg_root / "config.toml"`. Consistent. ✓

**4. Scope:** M0 is a single coherent deliverable (a working files layer). Graph/gate/dream/MCP/installer/viz are correctly deferred to M1–M6 — they are not referenced by any M0 task beyond naming.

No fixes needed beyond the flagged dead line in Task 5 (called out inline).

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-26-kg-m0-files-layer.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

After M0 ships, we plan **M1 (graph + normalization gate)** — the heart of the system — as the next plan.
