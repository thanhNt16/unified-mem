"""Keyword-grounded answer-quality rubric (M6 T3).

Deterministic string-containment scoring. No LLM, no network, no model.

Subscores (each in [0, 1]):
- cross_doc_hits  : fraction of expected cross-doc entity names present in answer
- multi_hop_reach : fraction of expected multi-hop entity names present in answer
- lineage         : fraction of expected source-doc references cited

Composite score is a fixed weighted combination of the three subscores.
All checks are case-insensitive, whitespace-normalized substring matches.
"""
from __future__ import annotations

import re
from typing import Mapping, Sequence

__all__ = ["score_answer"]


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace; None/empty -> empty string."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())


def _fraction_present(answer_norm: str, needles: Sequence[str] | None) -> float:
    """Fraction of `needles` found as substrings in `answer_norm`.

    Empty/None needle list -> 0.0 (nothing to find, nothing to credit).
    """
    if not needles:
        return 0.0
    hits = sum(1 for n in needles if n and _normalize(n) in answer_norm)
    return hits / len(needles)


def _subscore(answer_norm: str, expected: Mapping[str, Sequence[str]], key: str) -> float:
    """Pull list `key` from `expected` (also accept common aliases) and score."""
    if not isinstance(expected, Mapping):
        return 0.0
    raw = expected.get(key)
    if raw is None and key == "cross_doc":
        raw = expected.get("cross_doc_entities")
    if raw is None and key == "multi_hop":
        raw = expected.get("multi_hop_entities")
    if raw is None and key == "lineage":
        raw = expected.get("source_docs")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return 0.0
    return _fraction_present(answer_norm, list(raw))


# Fixed composite weights — ponytail: if user-driven weighting is needed later,
# promote to function params. Sums to 1.0.
_W_CROSS_DOC = 0.4
_W_MULTI_HOP = 0.4
_W_LINEAGE = 0.2


def score_answer(answer: str, expected: dict) -> dict:
    """Deterministic keyword-grounded scoring of `answer` against `expected`.

    Args:
        answer: free-text answer to score.
        expected: dict with optional sequence-valued keys:
            - cross_doc / cross_doc_entities : cross-document entity names
            - multi_hop / multi_hop_entities : multi-hop entity names
            - lineage / source_docs          : expected source-doc references

    Returns:
        Dict with subscores `cross_doc_hits`, `multi_hop_reach`, `lineage`
        (each in [0, 1]) and composite `score` in [0, 1].

    Notes:
        - Pure: no I/O, no model, no network.
        - Deterministic: same (answer, expected) -> identical output.
        - Missing answer -> 0 on all subscores; empty `expected` -> 0.
    """
    answer_norm = _normalize(answer) if answer else ""
    cross_doc = _subscore(answer_norm, expected, "cross_doc")
    multi_hop = _subscore(answer_norm, expected, "multi_hop")
    lineage = _subscore(answer_norm, expected, "lineage")
    composite = _W_CROSS_DOC * cross_doc + _W_MULTI_HOP * multi_hop + _W_LINEAGE * lineage
    return {
        "cross_doc_hits": cross_doc,
        "multi_hop_reach": multi_hop,
        "lineage": lineage,
        "score": composite,
    }
