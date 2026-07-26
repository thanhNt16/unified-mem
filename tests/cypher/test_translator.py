from kg.cypher import CypherError, parse, translate
from kg.cypher.parser import BinaryOp, PropertyAccess, Identifier, Literal

import pytest
from kg.ontology import Node, Edge
from kg.storage.sqlite import SQLiteAdapter


def _adapter(tmp_path):
    a = SQLiteAdapter(tmp_path / "kg.db")
    a.upsert_nodes([
        Node(id="u:person:demis", type="person", name="Demis Hassabis", summary="DeepMind founder"),
        Node(id="u:person:sundar", type="person", name="Sundar Pichai", summary="Google CEO"),
        Node(id="u:organization:google", type="organization", name="Google", summary="Tech company"),
        Node(id="u:person:unknown", type="person", name="Nobody Smith", summary="Unknown person"),
    ])
    a.upsert_edges([
        Edge(id="u:person:sundar|employed_by|u:organization:google", semantic_type="employed_by"),
        Edge(id="u:person:demis|knows|u:person:sundar", semantic_type="knows"),
    ])
    return a


def test_match_where_return_contains(tmp_path):
    a = _adapter(tmp_path)
    rows = translate(parse('MATCH (n:person) WHERE n.name CONTAINS "Demis" RETURN n'), a)
    assert len(rows) == 1
    assert rows[0]["node_id"] == "u:person:demis"
    assert rows[0]["name"] == "Demis Hassabis"


def test_match_where_return_exact(tmp_path):
    a = _adapter(tmp_path)
    rows = translate(parse('MATCH (n:person) WHERE n.name = "Sundar Pichai" RETURN n'), a)
    assert len(rows) == 1
    assert rows[0]["node_id"] == "u:person:sundar"


def test_match_where_return_regex(tmp_path):
    a = _adapter(tmp_path)
    rows = translate(parse(r"MATCH (n:person) WHERE n.name =~ 'Demis' RETURN n"), a)
    assert len(rows) == 1
    assert rows[0]["node_id"] == "u:person:demis"


def test_match_where_id_lookup(tmp_path):
    a = _adapter(tmp_path)
    rows = translate(parse('MATCH (n) WHERE n.id = "u:person:demis" RETURN n'), a)
    assert len(rows) == 1
    assert rows[0]["name"] == "Demis Hassabis"


def test_match_where_missing_returns_empty(tmp_path):
    a = _adapter(tmp_path)
    rows = translate(parse('MATCH (n:person) WHERE n.name = "NONEXISTENT" RETURN n'), a)
    assert rows == []


def test_match_relationship_expand(tmp_path):
    a = _adapter(tmp_path)
    rows = translate(
        parse('MATCH (n:person)-[r:employed_by]->(o) WHERE n.name = "Sundar Pichai" RETURN o'),
        a,
    )
    assert len(rows) == 1
    assert rows[0]["node_id"] == "u:organization:google"


def test_return_with_limit(tmp_path):
    a = _adapter(tmp_path)
    ast = parse('MATCH (n:person) WHERE n.name CONTAINS "a" RETURN n LIMIT 1')
    rows = translate(ast, a)
    assert len(rows) <= 1


def test_return_with_order_by(tmp_path):
    a = _adapter(tmp_path)
    ast = parse('MATCH (n:person) WHERE n.name CONTAINS "i" RETURN n ORDER BY n.name ASC')
    rows = translate(ast, a)
    if len(rows) >= 2:
        assert rows[0]["name"] <= rows[1]["name"]


def test_optional_match(tmp_path):
    a = _adapter(tmp_path)
    ast = parse(
        'OPTIONAL MATCH (n:person) WHERE n.name = "Demis Hassabis" RETURN n'
    )
    assert ast.match.optional
    rows = translate(ast, a)
    assert len(rows) == 1


def test_rejects_match_without_where(tmp_path):
    a = _adapter(tmp_path)
    ast = parse("MATCH (n:person) RETURN n")
    with pytest.raises(CypherError, match="WHERE predicate"):
        translate(ast, a)


def test_return_row_structure(tmp_path):
    a = _adapter(tmp_path)
    rows = translate(parse('MATCH (n:person) WHERE n.name CONTAINS "Demis" RETURN n'), a)
    row = rows[0]
    assert "node_id" in row
    assert "name" in row
    assert "type" in row
    assert "summary" in row
    assert row["type"] == "person"


def test_distinct_return(tmp_path):
    a = _adapter(tmp_path)
    ast = parse('MATCH (n:person) WHERE n.name CONTAINS "Demis" RETURN DISTINCT n')
    rows = translate(ast, a)
    assert len(rows) == 1
