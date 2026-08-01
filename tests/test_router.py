"""Tests for kg.router module."""

from kg.router import (
    QueryPolicy,
    HeuristicIntentClassifier,
    LLMIntentClassifier,
    classify,
    INTENT_HOPS,
    INTENT_CAP,
    INTENT_WEIGHTS,
)


class MockDriver:
    """Mock LLM driver for testing."""

    def __init__(self, result="trace"):
        self.result = result

    def complete(self, prompt):
        if "raise" in self.result:
            raise RuntimeError("LLM failed")
        return self.result


def test_heuristic_trace_keywords():
    """Heuristic classifier detects trace intent."""
    c = HeuristicIntentClassifier()
    assert c.classify("what calls foo") == "trace"
    assert c.classify("who calls bar") == "trace"
    assert c.classify("depends on baz") == "trace"
    assert c.classify("who depends on X") == "trace"
    assert c.classify("trace function y") == "trace"
    assert c.classify("callers of z") == "trace"
    assert c.classify("callees of w") == "trace"
    assert c.classify("data flow for v") == "trace"


def test_heuristic_explain_keywords():
    """Heuristic classifier detects explain intent."""
    c = HeuristicIntentClassifier()
    assert c.classify("explain foo") == "explain"
    assert c.classify("how does bar work") == "explain"
    assert c.classify("how do I baz") == "explain"
    assert c.classify("why does qux fail") == "explain"
    assert c.classify("why do we need this") == "explain"
    assert c.classify("architecture of X") == "explain"
    assert c.classify("what does function do") == "explain"
    assert c.classify("overview of module Y") == "explain"


def test_heuristic_find_default():
    """Heuristic classifier defaults to find for unmatched queries."""
    c = HeuristicIntentClassifier()
    assert c.classify("find users") == "find"
    assert c.classify("search for files") == "find"
    assert c.classify("list all items") == "find"
    assert c.classify("random query") == "find"
    assert c.classify("") == "find"


def test_query_policy_fields():
    """QueryPolicy has correct fields for each mode."""
    # find mode
    p_find = QueryPolicy(
        mode="find",
        hops=INTENT_HOPS["find"],
        cap=INTENT_CAP["find"],
        budget_tokens=2000,
        weights=INTENT_WEIGHTS["find"],
    )
    assert p_find.mode == "find"
    assert p_find.hops == 1
    assert p_find.cap == 20
    assert p_find.weights == {"lexical": 0.4, "semantic": 0.4, "graph": 0.2}
    assert p_find.budget_tokens == 2000

    # trace mode
    p_trace = QueryPolicy(
        mode="trace",
        hops=INTENT_HOPS["trace"],
        cap=INTENT_CAP["trace"],
        budget_tokens=2000,
        weights=INTENT_WEIGHTS["trace"],
    )
    assert p_trace.mode == "trace"
    assert p_trace.hops == 3
    assert p_trace.cap == 50
    assert p_trace.weights == {"lexical": 0.2, "semantic": 0.2, "graph": 0.6}

    # explain mode
    p_explain = QueryPolicy(
        mode="explain",
        hops=INTENT_HOPS["explain"],
        cap=INTENT_CAP["explain"],
        budget_tokens=2000,
        weights=INTENT_WEIGHTS["explain"],
    )
    assert p_explain.mode == "explain"
    assert p_explain.hops == 2
    assert p_explain.cap == 40
    assert p_explain.weights == {"lexical": 0.33, "semantic": 0.33, "graph": 0.34}


def test_llm_classifier_valid_result():
    """LLM classifier uses LLM result when valid."""
    driver = MockDriver(result="trace")
    c = LLMIntentClassifier(driver)
    assert c.classify("any query") == "trace"


def test_llm_classifier_fallback_on_invalid():
    """LLM classifier falls back to heuristic on invalid LLM result."""
    driver = MockDriver(result="invalid")
    c = LLMIntentClassifier(driver)
    # "what calls" is trace in heuristic
    assert c.classify("what calls foo") == "trace"


def test_llm_classifier_fallback_on_exception():
    """LLM classifier falls back to heuristic when LLM raises exception."""
    driver = MockDriver(result="raise")
    c = LLMIntentClassifier(driver)
    # Should fall back to heuristic
    assert c.classify("explain foo") == "explain"


def test_llm_classifier_no_driver():
    """LLM classifier falls back to heuristic when driver is None."""
    c = LLMIntentClassifier(driver=None)
    assert c.classify("trace bar") == "trace"


def test_classify_no_config():
    """classify() uses heuristic when config is None."""
    policy = classify("what calls foo", config=None)
    assert policy.mode == "trace"
    assert policy.hops == 3
    assert policy.cap == 50
    assert policy.budget_tokens == 2000


def test_classify_config_no_driver():
    """classify() uses heuristic when config has no driver."""
    class MockConfig:
        query = type("obj", (object,), {"intent_llm": True})()

    config = MockConfig()
    policy = classify("explain bar", config=config)
    assert policy.mode == "explain"
    assert policy.hops == 2


def test_classify_config_with_driver():
    """classify() uses LLM when config has driver."""
    class MockQueryConfig:
        intent_llm = True
        pack_budget_tokens = 3000

    class MockConfig:
        query = MockQueryConfig()
        driver = MockDriver(result="explain")

    config = MockConfig()
    policy = classify("any query", config=config)
    assert policy.mode == "explain"
    assert policy.budget_tokens == 3000


def test_classify_reads_budget_from_config():
    """classify() reads pack_budget_tokens from config."""
    class MockQueryConfig:
        intent_llm = False
        pack_budget_tokens = 5000

    class MockConfig:
        query = MockQueryConfig()

    config = MockConfig()
    policy = classify("find X", config=config)
    assert policy.budget_tokens == 5000


def test_classify_unknown_intent_defaults():
    """classify() defaults to find parameters for unknown intent."""
    # LLM returns unknown mode, should fallback to heuristic's "find"
    class MockQueryConfig:
        intent_llm = True
        pack_budget_tokens = 1000

    class MockConfig:
        query = MockQueryConfig()
        driver = MockDriver(result="unknown")

    config = MockConfig()
    policy = classify("random query", config=config)
    # Falls back to heuristic which returns "find" for random queries
    assert policy.mode == "find"
    assert policy.hops == 1
    assert policy.cap == 20


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-x"])
