import pytest
from kg.storage.base import StorageAdapter, Subgraph
from kg.ontology import Node


def test_subgraph_holds_nodes_and_edges():
    sg = Subgraph(nodes=[Node(type="person", name="A")], edges=[])
    assert len(sg.nodes) == 1
    assert sg.edges == []


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        StorageAdapter()  # type: ignore[abstract]
