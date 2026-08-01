from bench.evaluate_cbm import evaluate, parse_scale_100k


def test_evaluation_fails_when_graph_query_exceeds_floor():
    result = evaluate({
        "recall_at_5": 1.0, "recall_at_10": 1.0, "mrr": 1.0,
        "build_seconds": 3.4, "search_ms": 2.4, "query_ms": 201,
        "graph_hot_ms": 131,
    })
    assert result.ok is False
    assert "query_ms" in result.failures


def test_evaluation_passes_current_baseline():
    result = evaluate({
        "recall_at_5": 1.0, "recall_at_10": 1.0, "mrr": 0.96,
        "build_seconds": 3.4, "search_ms": 2.4, "query_ms": 33,
        "graph_hot_ms": 131,
    })
    assert result.ok is True


def test_parse_scale_100k_extracts_all_ceiling_metrics():
    output = """build: nodes 2.1s (100000), edges 1.3s (150000)
search: 2.5ms, hits=1
query (find): 29.2ms, budget=4000
/graph.json: 127.9ms, nodes=2000
"""
    assert parse_scale_100k(output) == {
        "build_seconds": 2.1,
        "search_ms": 2.5,
        "query_ms": 29.2,
        "graph_hot_ms": 127.9,
    }


def test_parse_scale_100k_rejects_missing_metric():
    try:
        parse_scale_100k("search: 2.5ms")
    except ValueError as exc:
        assert "build_seconds" in str(exc)
    else:
        raise AssertionError("expected missing metric error")
