# tests/test_ids.py
from kg.ids import node_id, edge_id


def test_node_id_format():
    assert node_id("quan", "person", "Demis Hassabis") == "quan:person:demis-hassabis"


def test_node_id_stable_across_surface_forms():
    a = node_id("quan", "person", "Demis Hassabis")
    b = node_id("quan", "person", "demis hassabis")
    assert a == b


def test_node_id_hashes_empty_unicode_or_punctuation_slugs():
    tokyo = node_id("u", "person", "東京")
    beijing = node_id("u", "person", "北京")
    punctuation = node_id("u", "person", "!!")
    assert tokyo != beijing
    assert tokyo and beijing
    assert punctuation == node_id("u", "person", "!!")
    assert punctuation


def test_edge_id_format_and_idempotence():
    eid = edge_id("quan:person:a", "employed_by", "quan:organization:b")
    assert eid == "quan:person:a|employed_by|quan:organization:b"
    assert edge_id("quan:person:a", "employed_by", "quan:organization:b") == eid
