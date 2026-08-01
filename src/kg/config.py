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


class DedupWeights(BaseModel):
    embedding: float = 0.7
    fuzzy: float = 0.3


class ThresholdsConfig(BaseModel):
    resolve_fuzzy: float = 0.85
    resolve_semantic: float = 0.80
    dedup_merge: float = 0.95
    dedup_flag: float = 0.85
    dedup_weights: DedupWeights = Field(default_factory=DedupWeights)


class ChunkingConfig(BaseModel):
    tokens: int = 512
    overlap: int = 64


class QueryConfig(BaseModel):
    rrf_k: int = 60
    default_hops: int = 2
    deep_search_hops: int = 3
    pack_budget_tokens: int = 4000
    subgraph_cap: int = 300
    traverse_budget: int = 500
    diversity_cap: int = 3
    graph_hops_find: int = 1
    graph_hops_trace: int = 3
    graph_hops_explain: int = 2
    intent_llm: bool = True


class IndexConfig(BaseModel):
    mode: str = "moderate"
    incremental_threshold: float = 0.10
    batch_size: int = 1000


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
    index: IndexConfig = IndexConfig()
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
        p = self.project
        b = self.backend
        e = self.embedding
        t = self.thresholds
        c = self.chunking
        q = self.query
        i = self.index
        d = self.dream
        embed_fields_lines = "\n".join(
            f"{k} = {v!r}" for k, v in e.embed_fields.items()
        ) or "# (none)"
        return f"""[project]
scope = "{p.scope}"
user_id = "{p.user_id}"

[backend]
kind = "{b.kind}"
path = "{b.path}"

[embedding]
provider = "{e.provider}"
model = "{e.model}"

[embedding.embed_fields]
{embed_fields_lines}

[thresholds]
resolve_fuzzy = {t.resolve_fuzzy}
resolve_semantic = {t.resolve_semantic}
dedup_merge = {t.dedup_merge}
dedup_flag = {t.dedup_flag}

[thresholds.dedup_weights]
embedding = {t.dedup_weights.embedding}
fuzzy = {t.dedup_weights.fuzzy}

[chunking]
tokens = {c.tokens}
overlap = {c.overlap}

[query]
rrf_k = {q.rrf_k}
default_hops = {q.default_hops}
deep_search_hops = {q.deep_search_hops}
pack_budget_tokens = {q.pack_budget_tokens}
subgraph_cap = {q.subgraph_cap}
traverse_budget = {q.traverse_budget}

[index]
mode = "{i.mode}"
incremental_threshold = {i.incremental_threshold}
batch_size = {i.batch_size}

[dream]
recent_window = "{d.recent_window}"
auto_hook = {str(d.auto_hook).lower()}
"""
