"""Tests for cascade advisory pre-filter."""
from __future__ import annotations

from kg.cascade import (
    ScoredChunk,
    _regex_score,
    _strip_markdown,
    prioritize_chunks,
)


# --- markdown stripping ---------------------------------------------------

def test_strip_markdown_removes_headings():
    # Headings stripped entirely to prevent false-positive proper-noun matches.
    assert _strip_markdown("# Title").strip() == ""
    assert _strip_markdown("## Sub Too").strip() == ""


def test_strip_markdown_removes_bold():
    # Bold/italic syntax dropped entirely to avoid false-positive matches.
    assert _strip_markdown("**bold**").strip() == ""
    assert _strip_markdown("*italic*").strip() == ""


def test_strip_markdown_removes_links():
    assert _strip_markdown("[text](http://x)").strip() == ""
    assert _strip_markdown("[Alice](http://x)").strip() == ""


def test_strip_markdown_removes_inline_code():
    assert _strip_markdown("`code`").strip() == ""


# --- false-positive guard -------------------------------------------------

def test_bold_text_not_false_positive():
    # Bold words without entities should score ~0.
    score = _regex_score("**quick brown fox**")
    assert score == 0.0


def test_heading_text_not_false_positive():
    score = _regex_score("# Section Header")
    assert score == 0.0


def test_link_text_not_false_positive():
    score = _regex_score("[Click Here](http://example.com)")
    assert score == 0.0


def test_proper_noun_still_scored():
    # Real entity sequence should score > 0.
    score = _regex_score("Alice Johnson met Bob Smith.")
    assert score > 0.0


# --- determinism ----------------------------------------------------------

def test_prioritize_deterministic():
    chunks = ["Alice Johnson codes.", "nothing here.", "Bob Smith builds."]
    r1 = prioritize_chunks(chunks)
    r2 = prioritize_chunks(chunks)
    assert [c.index for c in r1] == [c.index for c in r2]
    # Entity-bearing chunks rank first.
    assert r1[0].index in (0, 2)
    assert r1[-1].index == 1  # no-entity chunk last


def test_prioritize_preserves_all_chunks():
    chunks = ["a", "b", "c"]
    out = prioritize_chunks(chunks)
    assert len(out) == 3
    assert {c.text for c in out} == set(chunks)


# --- advisory, not gating -------------------------------------------------

def test_budget_never_drops_chunks():
    chunks = ["Alice Johnson.", "plain text here.", "Bob Smith today."]
    out = prioritize_chunks(chunks, budget=1)
    assert len(out) == 3  # all chunks still present despite budget=1


def test_budget_reorders_high_first():
    chunks = ["nothing here.", "Alice Johnson codes.", "more nothing."]
    out = prioritize_chunks(chunks, budget=1)
    # Top-scored chunk first.
    assert out[0].score > 0
    # Remaining chunks follow in original order.
    tail_indices = [c.index for c in out[1:]]
    assert tail_indices == sorted(tail_indices)


# --- bounded input --------------------------------------------------------

def test_bounded_input_large_string():
    big = "x" * 1_000_000
    # Should not raise / hang; result may be empty/zero.
    out = prioritize_chunks([big])
    assert len(out) == 1
    assert out[0].score == 0.0


def test_empty_chunks():
    assert prioritize_chunks([]) == []


# --- spaCy fallback -------------------------------------------------------

def test_spacy_absent_falls_back_to_regex():
    # _regex_score is the fallback. Force it; spaCy optional in env.
    # If spaCy present, _spacy_score path is exercised in prioritize.
    # Here we directly verify regex path produces sane result.
    assert _regex_score("Alice Johnson met Bob Smith.") > 0
    assert _regex_score("just words here.") == 0
