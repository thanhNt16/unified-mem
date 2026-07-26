from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")
_HEADING = re.compile(r"^(?:#{1,6})\s+", re.MULTILINE)


def slugify(text: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s:
        # Non-ASCII / pure-punctuation names collapse to "". Fall back to a
        # stable short hash so different names produce different IDs and the
        # slug is never empty (which would collide at "{user}:{type}:").
        return "x" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return s[:max_len]


@dataclass(frozen=True)
class Chunk:
    index: int
    start_char: int
    end_char: int
    text: str
    token_count: int


def _heading_offsets(text: str) -> list[int]:
    return [m.start() for m in _HEADING.finditer(text)]


def _split_to_segments(text: str) -> list[tuple[int, int]]:
    """Return (start_char, end_char) segments that begin at each heading."""
    offs = _heading_offsets(text)
    if not offs or offs[0] != 0:
        offs = [0, *offs]
    segs = []
    for i, start in enumerate(offs):
        end = offs[i + 1] if i + 1 < len(offs) else len(text)
        if end > start:
            segs.append((start, end))
    return segs


def _fill(seg_text: str, tokens: int, overlap: int) -> list[tuple[int, int]]:
    """Greedy token-budget fill of one segment's text -> list[(start_char, end_char)]."""
    ids = _ENC.encode(seg_text)
    if len(ids) <= tokens:
        return [(0, len(seg_text))]
    spans: list[tuple[int, int]] = []
    step = max(1, tokens - overlap)
    i = 0
    n = len(ids)
    while i < n:
        window_end = min(i + tokens, n)
        start_char = len(_ENC.decode(ids[:i]))
        end_char = len(_ENC.decode(ids[:window_end]))
        spans.append((start_char, end_char))
        if window_end >= n:
            break
        i += step
    return spans


def chunk_markdown(text: str, tokens: int = 512, overlap: int = 64) -> list[Chunk]:
    if not text.strip():
        return []
    chunks: list[Chunk] = []
    idx = 0
    for seg_start, seg_end in _split_to_segments(text):
        seg_text = text[seg_start:seg_end]
        for (a, b) in _fill(seg_text, tokens=tokens, overlap=overlap):
            chunk_text = seg_text[a:b]
            if not chunk_text.strip():
                continue
            end_char = min(seg_start + b, len(text))
            chunks.append(Chunk(
                index=idx,
                start_char=seg_start + a,
                end_char=end_char,
                text=text[seg_start + a:end_char],
                token_count=len(_ENC.encode(chunk_text)),
            ))
            idx += 1
    return chunks
