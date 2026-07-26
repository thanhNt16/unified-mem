import hashlib
from kg.embed import FakeEmbedder, content_hash, make_embedder
from kg.config import Config


def test_fake_embedder_deterministic():
    e = FakeEmbedder(dim=8)
    v1 = e.embed("hello world")
    v2 = e.embed("hello world")
    assert v1 == v2
    assert len(v1) == 8


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
    from kg.embed import cosine_similarity

    assert cosine_similarity([2.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([0.0], [1.0]) == 0.0
