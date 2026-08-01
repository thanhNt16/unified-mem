from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class QueryPolicy:
    mode: str  # "find" | "trace" | "explain"
    hops: int  # find=1, trace=3, explain=2
    cap: int  # max nodes fetched
    budget_tokens: int  # effective budget
    weights: dict  # {"lexical":float,"semantic":float,"graph":float}


# Intent → defaults (evidence-based, agentmemory + spec)
INTENT_WEIGHTS = {
    "find": {"lexical": 0.4, "semantic": 0.4, "graph": 0.2},
    "trace": {"lexical": 0.2, "semantic": 0.2, "graph": 0.6},
    "explain": {"lexical": 0.33, "semantic": 0.33, "graph": 0.34},
}

INTENT_HOPS = {"find": 1, "trace": 3, "explain": 2}

INTENT_CAP = {"find": 20, "trace": 50, "explain": 40}


class IntentClassifier(Protocol):
    def classify(self, query: str) -> str: ...


class HeuristicIntentClassifier:
    """Regex/keyword rules. Deterministic, offline-safe. Default fallback."""

    def classify(self, query: str) -> str:
        q = query.lower()

        # trace: call chains, dependencies, data flow
        if any(
            k in q
            for k in (
                "what calls",
                "who calls",
                "depends on",
                "who depends",
                "trace",
                "callers of",
                "callees of",
                "data flow",
            )
        ):
            return "trace"

        # explain: how/why/architecture
        if any(
            k in q
            for k in (
                "explain",
                "how does",
                "how do",
                "why does",
                "why do",
                "architecture of",
                "what does",
                "overview of",
            )
        ):
            return "explain"

        # default
        return "find"


class LLMIntentClassifier:
    """Calls harness LLM with 3-shot prompt. Only used when driver configured."""

    def __init__(self, driver=None):
        self.driver = driver

    def classify(self, query: str) -> str:
        if self.driver is None:
            return HeuristicIntentClassifier().classify(query)

        try:
            prompt = f"""Classify query intent. Return ONE word: find, trace, or explain.

Query: "{query}"

Rules:
- trace: "what calls X", "who depends on X", "callers of", "callees of", data flow
- explain: "explain X", "how does X work", "why does X", architecture of, overview of
- find: everything else

Intent:"""
            result = self.driver.complete(prompt).strip().lower()
            # Validate result; fall back if invalid
            if result in ("find", "trace", "explain"):
                return result
        except Exception:
            pass

        return HeuristicIntentClassifier().classify(query)


def classify(query: str, config=None) -> QueryPolicy:
    """Return QueryPolicy for query using LLM if available, else heuristic."""
    # Try LLM if config.query.intent_llm AND driver configured
    if config is not None:
        query_cfg = getattr(config, "query", None)
        if query_cfg is not None and getattr(query_cfg, "intent_llm", True):
            # Check for driver attribute (duck-typed LLM)
            driver = getattr(config, "driver", None)
            if driver is not None:
                mode = LLMIntentClassifier(driver).classify(query)
            else:
                mode = HeuristicIntentClassifier().classify(query)
        else:
            mode = HeuristicIntentClassifier().classify(query)
    else:
        mode = HeuristicIntentClassifier().classify(query)

    # Lookup intent parameters
    hops = INTENT_HOPS.get(mode, 1)
    cap = INTENT_CAP.get(mode, 20)
    weights = INTENT_WEIGHTS.get(mode, INTENT_WEIGHTS["find"])

    # Get budget from config if available
    if config is not None:
        query_cfg = getattr(config, "query", None)
        if query_cfg is not None:
            budget = getattr(query_cfg, "pack_budget_tokens", 2000)
        else:
            budget = 2000
    else:
        budget = 2000

    return QueryPolicy(
        mode=mode, hops=hops, cap=cap, budget_tokens=budget, weights=weights
    )
