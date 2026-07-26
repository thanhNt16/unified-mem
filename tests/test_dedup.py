from kg.config import Config
from kg.dedup import Deduper, full_context_text
from kg.embed import FakeEmbedder
from kg.ontology import Node
from kg.storage.sqlite import SQLiteAdapter


def test_full_context_text_includes_high_signal_attrs():
    node = Node(
        type="person",
        name="Demis",
        summary="CEO",
        attributes={"role": "founder", "email": "x@y.com"},
    )
    text = full_context_text(node, {"person": ["name", "summary", "attributes.role"]})
    assert "Demis" in text and "CEO" in text and "founder" in text
    assert "x@y.com" not in text


def test_dedup_high_score_for_near_duplicate(tmp_path):
    cfg = Config.default()
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    embedder = FakeEmbedder()
    node1 = Node(
        id="u:person:p1",
        type="person",
        name="Paris",
        canonical_name="Paris",
        summary="capital of France",
    )
    adapter.upsert_nodes([node1])
    deduper = Deduper(adapter, embedder, cfg.thresholds)
    node2 = Node(
        id="u:person:p2",
        type="person",
        name="Paris",
        canonical_name="Paris",
        summary="capital of France",
    )
    result = deduper.dedup(node2)
    assert result.best_match_id == "u:person:p1"
    assert result.score >= 0.85


def test_dedup_low_score_for_distinct(tmp_path):
    cfg = Config.default()
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    embedder = FakeEmbedder()
    adapter.upsert_nodes([
        Node(
            id="u:person:a",
            type="person",
            name="Paris",
            summary="capital of France",
        )
    ])
    deduper = Deduper(adapter, embedder, cfg.thresholds)
    node2 = Node(
        id="u:person:b",
        type="person",
        name="Paris",
        summary="city in Texas, USA",
    )
    result = deduper.dedup(node2)
    assert result.score < 0.85


class _NonNormalizedEmbedder:
    def embed(self, text):
        # unrelated text gets a huge-magnitude vector; if dot product were
        # used unnormalized this would inflate the score past merge/flag.
        return {
            "Paris capital of France": [100.0, *([0.0] * 383)],
            "Unrelated distinct city": [0.1, 1.0, *([0.0] * 382)],
        }[text]

    def embed_many(self, texts):
        return [self.embed(text) for text in texts]

    def dim(self):
        return 2


def test_dedup_non_normalized_embeddings_cannot_inflate_score(tmp_path):
    # Same canonical_name forces candidacy regardless of vec_search, so this
    # isolates the cosine computation itself: a huge-magnitude unrelated
    # vector must not inflate the score via raw dot product.
    cfg = Config.default()
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    embedder = _NonNormalizedEmbedder()
    adapter.upsert_nodes([
        Node(
            id="u:person:a",
            type="person",
            name="Paris",
            canonical_name="shared",
            summary="capital of France",
        )
    ])
    deduper = Deduper(adapter, embedder, cfg.thresholds)
    node2 = Node(
        id="u:person:b",
        type="person",
        name="Unrelated",
        canonical_name="shared",
        summary="distinct city",
    )
    result = deduper.dedup(node2)
    assert result.best_match_id == "u:person:a"
    assert result.score < cfg.thresholds.dedup_flag