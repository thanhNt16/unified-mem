"""Deterministic keyword-grounded rubric tests (M6 T3).

All checks are string-containment — no LLM-judge behavior. These tests are
pure and deterministic; no network, no model.
"""
from __future__ import annotations

import copy
import random

import pytest

from bench.rubric import score_answer


# --- subscore range ---------------------------------------------------------

def test_subscores_in_zero_one_range():
    r = score_answer(
        "Acme Corp merged with Globex and cited raw/notes.md.",
        {
            "cross_doc": ["Acme Corp", "Globex"],
            "multi_hop": ["Initech"],
            "lineage": ["raw/notes.md"],
        },
    )
    for k in ("cross_doc_hits", "multi_hop_reach", "lineage", "score"):
        assert 0.0 <= r[k] <= 1.0, f"{k} out of [0,1]: {r[k]}"


def test_composite_is_weighted_sum():
    r = score_answer(
        "Acme Globex Initech raw/notes.md",  # all present
        {
            "cross_doc": ["Acme", "Globex", "Initech"],  # 1.0
            "multi_hop": ["Initech"],  # 1.0
            "lineage": ["raw/notes.md"],  # 1.0
        },
    )
    # 0.4*1.0 + 0.4*1.0 + 0.2*1.0 == 1.0
    assert r["score"] == pytest.approx(1.0)
    # weighted formula spot-check
    expected = 0.4 * r["cross_doc_hits"] + 0.4 * r["multi_hop_reach"] + 0.2 * r["lineage"]
    assert r["score"] == pytest.approx(expected)


# --- case-insensitivity + whitespace normalization ---------------------------

def test_case_insensitive_and_whitespace_normalized():
    r1 = score_answer(
        "ACME   CORP\tmerged with GLOBEX",
        {"cross_doc": ["acme corp", "globex"]},
    )
    r2 = score_answer(
        "acme corp merged with globex",
        {"cross_doc": ["Acme Corp", "Globex"]},
    )
    assert r1["cross_doc_hits"] == 1.0
    assert r2["cross_doc_hits"] == 1.0
    assert r1["score"] == r2["score"]


# --- boundary cases ---------------------------------------------------------

def test_missing_answer_scores_zero():
    for ans in (None, ""):
        r = score_answer(
            ans,  # type: ignore[arg-type]
            {"cross_doc": ["Acme"], "multi_hop": ["Globex"], "lineage": ["raw/x.md"]},
        )
        assert r["score"] == 0.0
        assert r["cross_doc_hits"] == 0.0
        assert r["multi_hop_reach"] == 0.0
        assert r["lineage"] == 0.0


def test_perfect_answer_scores_one():
    r = score_answer(
        "Acme Globex Initech raw/x.md raw/y.md",
        {
            "cross_doc": ["Acme", "Globex"],
            "multi_hop": ["Initech"],
            "lineage": ["raw/x.md", "raw/y.md"],
        },
    )
    assert r["cross_doc_hits"] == 1.0
    assert r["multi_hop_reach"] == 1.0
    assert r["lineage"] == 1.0
    assert r["score"] == pytest.approx(1.0)


def test_empty_expected_scores_zero():
    r = score_answer("anything goes here", {})
    assert r["score"] == 0.0
    assert r["cross_doc_hits"] == 0.0
    assert r["multi_hop_reach"] == 0.0
    assert r["lineage"] == 0.0


# --- partial credit ---------------------------------------------------------

def test_partial_credit_cross_doc():
    # 2 of 3 cross-doc entities present -> 2/3
    r = score_answer(
        "Acme and Globex",
        {"cross_doc": ["Acme", "Globex", "Initech"]},
    )
    assert r["cross_doc_hits"] == pytest.approx(2 / 3)
    # composite is 0.4 * 2/3 (multi_hop and lineage stay 0)
    assert r["score"] == pytest.approx(0.4 * 2 / 3)


def test_partial_credit_lineage():
    # 1 of 2 source docs cited -> 0.5
    r = score_answer(
        "see raw/alpha.md for context",
        {"lineage": ["raw/alpha.md", "raw/beta.md"]},
    )
    assert r["lineage"] == 0.5
    assert r["score"] == pytest.approx(0.2 * 0.5)


# --- lineage checks source-doc refs ----------------------------------------

def test_lineage_checks_source_doc_references():
    r_hit = score_answer(
        "Conclusion per raw/deep/a.md and raw/deep/b.md.",
        {"lineage": ["raw/deep/a.md", "raw/deep/b.md"]},
    )
    assert r_hit["lineage"] == 1.0

    r_miss = score_answer(
        "Conclusion based on internal reasoning.",
        {"lineage": ["raw/deep/a.md"]},
    )
    assert r_miss["lineage"] == 0.0


# --- determinism -----------------------------------------------------------

def test_deterministic_repeated_calls():
    args = (
        "Acme merged with Globex; see raw/x.md",
        {"cross_doc": ["Acme", "Globex"], "multi_hop": ["Initech"], "lineage": ["raw/x.md"]},
    )
    baseline = score_answer(*args)
    for _ in range(10):
        assert score_answer(*copy.deepcopy(args)) == baseline


def test_deterministic_under_input_shuffle():
    # Same content reshuffled must give same composite score.
    expected = {
        "cross_doc": ["Acme", "Globex"],
        "multi_hop": ["Initech"],
        "lineage": ["raw/x.md"],
    }
    s1 = score_answer("Acme Globex Initech raw/x.md", expected)
    s2 = score_answer("raw/x.md Initech Globex Acme", expected)
    assert s1 == s2


def test_deterministic_pseudorandom_inputs():
    rnd = random.Random(42)
    alphabet = "acme globex initech raw x md "
    for _ in range(50):
        ans = "".join(rnd.choice(alphabet) for _ in range(40))
        exp = {
            "cross_doc": ["acme", "globex"],
            "multi_hop": ["initech"],
            "lineage": ["raw/x.md"],
        }
        # Same seed -> same score across two freshly-seeded runs.
        assert score_answer(ans, exp) == score_answer(ans, copy.deepcopy(exp))


# --- alias keys -----------------------------------------------------------

def test_alias_keys_accepted():
    # canonical names preferred, but common aliases also work
    r_canon = score_answer(
        "Acme Globex Initech raw/x.md",
        {"cross_doc": ["Acme"], "multi_hop": ["Initech"], "lineage": ["raw/x.md"]},
    )
    r_alias = score_answer(
        "Acme Globex Initech raw/x.md",
        {
            "cross_doc_entities": ["Acme"],
            "multi_hop_entities": ["Initech"],
            "source_docs": ["raw/x.md"],
        },
    )
    assert r_canon == r_alias
