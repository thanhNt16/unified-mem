# tests/test_ids.py
from kg.ids import node_id, edge_id


def test_node_id_format():
    assert node_id("quan", "person", "Demis Hassabis") == "quan:person:demis-hassabis"


def test_node_id_stable_across_surface_forms():
    a = node_id("quan", "person", "Demis Hassabis")
    b = node_id("quan", "person", "demis hassabis")
    assert a == b


def test_edge_id_format_and_idempotence():
    eid = edge_id("quan:person:a", "employed_by", "quan:organization:b")
    assert eid == "quan:person:a|employed_by|quan:organization:b"
    assert edge_id("quan:person:a", "employed_by", "quan:organization:b") == eid
