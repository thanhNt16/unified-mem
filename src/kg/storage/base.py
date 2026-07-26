from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from kg.ontology import Node, Edge


@dataclass
class Subgraph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


class StorageAdapter(ABC):
    @abstractmethod
    def upsert_nodes(self, nodes: list[Node]) -> int: ...
    @abstractmethod
    def upsert_edges(self, edges: list[Edge]) -> int: ...
    @abstractmethod
    def get(self, node_id: str) -> Node | None: ...
    @abstractmethod
    def delete(self, node_id: str, tombstone: bool = True) -> None: ...
    @abstractmethod
    def neighbors(self, ids: list[str], depth: int = 1,
                  direction: str = "both",
                  edge_types: list[str] | None = None) -> Subgraph: ...
    @abstractmethod
    def fts_search(self, query: str, k: int = 10,
                   type_filter: str | None = None) -> list[tuple[str, float]]: ...
    @abstractmethod
    def vec_search(self, embedding: list[float], k: int = 10,
                   type_filter: str | None = None) -> list[tuple[str, float]]: ...
    @abstractmethod
    def count(self) -> dict: ...
    @abstractmethod
    def transaction(self): ...
