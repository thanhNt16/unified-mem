import pytest

from kg.cypher import CypherError, parse
from kg.cypher.parser import MAX_INPUT_CHARS


def test_parse_match_return():
    ast = parse("MATCH (n:person) RETURN n")
    assert ast.return_.items
    assert ast.match.patterns[0][0].labels == {"person"}


def test_read_only_clauses_parse():
    ast = parse(
        "OPTIONAL MATCH (n:person)-[r:knows]->(m:person) "
        "WHERE n.name CONTAINS 'Demis' "
        "WITH n, m RETURN n, m ORDER BY n.name DESC LIMIT 3"
    )
    assert ast.match.optional
    assert ast.where.depth == 0
    assert len(ast.with_clauses) == 1
    assert len(ast.return_.items) == 2
    assert ast.limit == 3


@pytest.mark.parametrize("query", [
    "CREATE (n)", "MERGE (n)", "DELETE n", "SET n.x=1", "DROP INDEX i",
    "REMOVE n.x", "CALL db.schema()", "MATCH (n) CALL db.labels() RETURN n",
    "MATCH (n) DELETE n RETURN n",
])
def test_rejects_write_clause_at_token_level(query):
    with pytest.raises(CypherError, match=r"^read-only: .* not allowed at offset "):
        parse(query)


def test_rejects_oversized_input():
    with pytest.raises(CypherError, match=rf"^query too long: {MAX_INPUT_CHARS + 1} >"):
        parse("M" * (MAX_INPUT_CHARS + 1))


def test_rejects_201_return_items():
    items = ", ".join(f"n AS n{i}" for i in range(201))
    with pytest.raises(CypherError, match=r"^too many return items: 201 > 200$"):
        parse(f"MATCH (n) RETURN {items}")


def test_rejects_depth_eleven_where():
    expr = "n.name = 'Demis'"
    for _ in range(11):
        expr = f"NOT ({expr})"
    with pytest.raises(CypherError, match=r"^WHERE nesting depth 11 exceeds max 10$"):
        parse(f"MATCH (n) WHERE {expr} RETURN n")


def test_error_is_deterministic():
    query = "MATCH (n) CALL db.labels() RETURN n"
    with pytest.raises(CypherError) as first:
        parse(query)
    with pytest.raises(CypherError) as second:
        parse(query)
    assert str(first.value) == str(second.value) == "read-only: CALL not allowed at offset 10"
