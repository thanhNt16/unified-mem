"""Advisory chunk prioritizer for extraction. Never blocks extraction, only reorders."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Max input chars processed — anything beyond is ignored (bounded).
_MAX_INPUT = 100_000

# Markdown syntax to strip before proper-noun matching. Drop entirely to avoid
# false-positive proper-noun matches on labels/heading text. (Markdown is
# structural, not content: entities live in body prose, not link labels.)
_MD_STRIP = re.compile(
    r"`[^`]*`"                         # inline code
    r"|\*{1,3}[^*]+\*{1,3}"            # bold/italic
    r"|\[[^\]]*\]\([^)]*\)"            # [text](url)
    r"|\[[^\]]*\]"                     # [text] alone
    r"|^#{1,6}\s+.*$"                  # heading line
    , re.MULTILINE,
)

# Proper-noun heuristic: 2+ consecutive Capitalized words (2+ chars each),
# allowing spaces/hyphens between them. Not matched right after sentence end.
_PROPER_NOUN = re.compile(
    r"(?<![.!?]\s)"
    r"[A-Z][a-z0-9]+(?:[-'][A-Za-z0-9]+)*"
    r"(?:\s+[A-Z][a-z0-9]+(?:[-'][A-Za-z0-9]+]*)*)+"
)

_SPACY_AVAILABLE = False
try:
    import spacy  # type: ignore[import-untyped]
    _SPACY_AVAILABLE = True
except ImportError:
    pass


@dataclass(frozen=True, order=True)
class ScoredChunk:
    """A chunk with its priority score. Higher score = process first."""
    score: float
    index: int
    text: str


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax that would false-positive the proper-noun regex."""
    return _MD_STRIP.sub(" ", text)


def _regex_score(text: str) -> float:
    """Score based on proper-noun density in stripped text. Deterministic."""
    stripped = _strip_markdown(text[:_MAX_INPUT])
    matches = _PROPER_NOUN.findall(stripped)
    if not matches:
        return 0.0
    total_chars = sum(len(m) for m in matches)
    return total_chars / max(len(stripped), 1)


def _spacy_score(text: str) -> float | None:
    """Score via spaCy NER if available. Returns None if unavailable."""
    if not _SPACY_AVAILABLE:
        return None
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        return None
    doc = nlp(text[:_MAX_INPUT])
    entity_chars = sum(ent.end_char - ent.start_char for ent in doc.ents)
    return entity_chars / max(len(doc.text), 1)


def prioritize_chunks(
    chunks: list[str],
    budget: int | None = None,
) -> list[ScoredChunk]:
    """Rank chunks by entity density. Advisory — never drops chunks.

    Args:
        chunks: Text chunks to prioritize.
        budget: If set, first *budget* chunks get "high" flag via ordering;
               all chunks are still returned. None = return all ranked.

    Returns:
        ScoredChunks sorted descending by score. All chunks present.
    """
    scored: list[ScoredChunk] = []
    for i, text in enumerate(chunks):
        score = _spacy_score(text)
        if score is None:
            score = _regex_score(text)
        scored.append(ScoredChunk(score=score, index=i, text=text))
    scored.sort(key=lambda sc: (sc.score, -sc.index), reverse=True)
    # budget only affects order emphasis — all chunks returned.
    if budget is not None and budget > 0:
        # Put budget-count high-score chunks first, rest follow (stable).
        high = scored[:budget]
        rest = scored[budget:]
        rest.sort(key=lambda sc: sc.index)  # restore original order for tail
        return high + rest
    return scored
