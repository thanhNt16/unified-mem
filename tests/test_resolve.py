from kg.config import Config
from kg.embed import FakeEmbedder
from kg.ontology import Node
from kg.resolve import Resolver, Resolution
from kg.storage.sqlite import SQLiteAdapter


def _setup(tmp_path):
    cfg = Config.default()
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    adapter.upsert_nodes([
        Node(
            id="u:person:demis-hassabis",
            type="person",
            name="Demis Hassabis",
            canonical_name="Demis Hassabis",
            aliases=["D. Hassabis"],
        ),
    ])
    return adapter, Resolver(adapter, FakeEmbedder(), cfg.thresholds)


def test_exact_alias(tmp_path):
    _, resolver = _setup(tmp_path)
    result = resolver.resolve("D. Hassabis", "person")
    assert result.via == "exact"
    assert result.matched_id == "u:person:demis-hassabis"


def test_fuzzy_match(tmp_path):
    _, resolver = _setup(tmp_path)
    result = resolver.resolve("demis hasabis", "person")
    assert result.via in ("fuzzy", "exact")
    assert result.matched_id == "u:person:demis-hassabis"


def test_no_match_returns_none_id(tmp_path):
    _, resolver = _setup(tmp_path)
    result = resolver.resolve("Completely Unrelated Person", "person")
    assert result.matched_id is None
    assert result.via == "none"


def test_type_gated(tmp_path):
    _, resolver = _setup(tmp_path)
    result = resolver.resolve("Demis Hassabis", "organization")
    assert result.matched_id is None


class _NonNormalizedEmbedder:
    def embed(self, text):
        return {"query": [100.0, 0.0], "candidate": [0.1, 1.0]}[text]

    def embed_many(self, texts):
        return [self.embed(text) for text in texts]

    def dim(self):
        return 2


def test_semantic_resolution_uses_normalized_cosine(tmp_path):
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    adapter.upsert_nodes([
        Node(id="u:person:candidate", type="person", name="candidate"),
    ])
    resolver = Resolver(
        adapter, _NonNormalizedEmbedder(), Config.default().thresholds
    )
    result = resolver.resolve("query", "person")
    assert result.matched_id is None
    assert result.via == "none"
