import pytest
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
    with pytest.raises(FileNotFoundError):
        Config.from_path(tmp_path / "nope.toml")
