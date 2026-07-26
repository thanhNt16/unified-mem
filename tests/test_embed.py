import hashlib
from kg.embed import (
    Embedder,
    FakeEmbedder,
    content_hash,
    cosine_similarity,
    make_embedder,
)
from kg.config import Config


def test_fake_embedder_deterministic():
    e = FakeEmbedder(dim=8)
    v1 = e.embed("hello world")
    v2 = e.embed("hello world")
    assert v1 == v2
    assert len(v1) == 8
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-6


def test_fake_embedder_different_inputs_differ():
    e = FakeEmbedder(dim=8)
    assert e.embed("aaa") != e.embed("bbb")


def test_embed_many_consistent_with_embed():
    e = FakeEmbedder(dim=8)
    single = e.embed("x")
    many = e.embed_many(["x", "y"])
    assert many[0] == single
    assert len(many) == 2


def test_content_hash_stable():
    assert content_hash("abc") == hashlib.sha256(b"abc").hexdigest()


def test_make_embedder_local_returns_embedder():
    cfg = Config.default()
    e = make_embedder(cfg)
    assert e.dim() == 384


def test_cosine_similarity_normalizes_and_handles_zero():
    assert cosine_similarity([2.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([0.0], [1.0]) == 0.0


def test_normalize_boundary_for_local_embedder(monkeypatch):
    """LocalEmbedder must L2-normalize its raw output so vec_search L2 ranking
    stays monotonic with cosine. We stub the ONNX embed call to return a
    non-unit vector and assert the public API emits unit-norm output."""
    from kg import embed as embed_mod

    class _FakeVec:
        def __init__(self, vals):
            self._vals = vals

        def tolist(self):
            return list(self._vals)

    class _FakeModel:
        def embed(self, texts):
            for t in texts:
                if t == "x":
                    yield _FakeVec([2.0, 0.0, 0.0, 0.0])
                else:
                    yield _FakeVec([0.0, 0.0, 2.0, 0.0])

    def fake_load(self):
        if self._model is None:
            self._model = _FakeModel()

    monkeypatch.setattr(embed_mod.LocalEmbedder, "_load", fake_load)

    e = embed_mod.LocalEmbedder()
    v = e.embed("x")
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    many = e.embed_many(["x", "y"])
    for vec in many:
        assert abs(sum(x * x for x in vec) ** 0.5 - 1.0) < 1e-6


def test_normalize_helper_zero_vector_safe():
    from kg.embed import _normalize

    assert _normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]
    v = _normalize([3.0, 4.0])
    assert abs(v[0] - 0.6) < 1e-9 and abs(v[1] - 0.8) < 1e-9
