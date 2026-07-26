from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

from kg.config import Config


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    a_norm = sum(x * x for x in a) ** 0.5
    b_norm = sum(y * y for y in b) ** 0.5
    return dot / (a_norm * b_norm) if a_norm and b_norm else 0.0


def _normalize(vec: list[float]) -> list[float]:
    """L2-normalize at the Embedder boundary so vec_search's L2 distance is
    monotonic with cosine. Zero-vectors returned unchanged (cannot normalize)."""
    norm = sum(x * x for x in vec) ** 0.5
    if not norm:
        return list(vec)
    return [x / norm for x in vec]


class Embedder(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    @abstractmethod
    def embed_many(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def dim(self) -> int: ...


class FakeEmbedder(Embedder):
    """Deterministic hash-based embedding for tests."""

    def __init__(self, dim: int = 384):
        self._dim = dim

    def _vec(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        out = [(h[i % len(h)] / 255.0) * 2 - 1 for i in range(self._dim)]
        norm = sum(x * x for x in out) ** 0.5 or 1.0
        return [x / norm for x in out]

    def embed(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def dim(self) -> int:
        return self._dim


class LocalEmbedder(Embedder):
    """fastembed wrapper — ONNX model lazy-loaded on first embed call."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self._model_name = model_name
        self._model = None
        self._dim: int = 384

    def _load(self) -> None:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)

    def embed(self, text: str) -> list[float]:
        self._load()
        return _normalize(next(self._model.embed([text])).tolist())

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        self._load()
        return [_normalize(v.tolist()) for v in self._model.embed(texts)]

    def dim(self) -> int:
        return self._dim


def make_embedder(config: Config) -> Embedder:
    if config.embedding.provider == "local":
        model = config.embedding.model
        if "/" not in model:
            model = f"BAAI/{model}"
        return LocalEmbedder(model_name=model)
    return FakeEmbedder()
