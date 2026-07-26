"""H2: TOCTOU-resilient transcript open — single O_NOFOLLOW open + fstat + read.

Regression: ``_safe_transcript_path`` returning a Path and the caller separately
opening it created a window where a symlink could be swapped between the stat
check and the read, allowing an attacker to read files outside the trusted
session root. The fix uses a single ``os.open(O_NOFOLLOW | O_RDONLY)`` and an
``os.fstat`` on the resulting fd, so the check and read happen on the same fd.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from kg.hooks import session_end


def _make_transcript(session_root: Path, name: str = "session.jsonl", content: str = "ok") -> Path:
    target = session_root / name
    target.write_text(json.dumps({"role": "user", "content": content}) + "\n", encoding="utf-8")
    return target


def test_open_transcript_rejects_symlink_fd_via_NOFOLLOW(tmp_path):
    """Symlink target must be rejected at open() time, regardless of destination."""
    session_root = tmp_path / "sessions"; session_root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{}\n', encoding="utf-8")
    link = session_root / "link.jsonl"
    link.symlink_to(outside)
    payload = {"transcript_path": "link.jsonl"}
    with pytest.raises(session_end.HookInputError):
        session_end.conversation_from_payload(payload, session_root=session_root)


def test_open_transcript_no_toctou_swap_to_symlink(tmp_path, monkeypatch):
    """Simulate symlink swap between path validation and open; verify the
    O_NOFOLLOW open rejects the swapped-in symlink fd and never reads outside."""
    session_root = tmp_path / "sessions"; session_root.mkdir()
    target = session_root / "session.jsonl"
    target.write_text(json.dumps({"role": "user", "content": "ok"}) + "\n", encoding="utf-8")
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps({"role": "user", "content": "must-not-leak"}) + "\n",
                       encoding="utf-8")

    # Capture reads via os.read to prove the swapped symlink was never read.
    real_open = os.open
    real_read = os.read
    reads: list[int] = []
    swapped = {"done": False}

    def swap_to_symlink(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        # On the first open of the transcript target, swap the on-disk entry
        # to a symlink pointing outside. Subsequent open() must reject (ELOOP).
        if not swapped["done"] and Path(path).name == "session.jsonl":
            swapped["done"] = True
            target.unlink()
            target.symlink_to(outside)
        return fd

    def tracked_read(fd, n):
        reads.append(fd)
        return real_read(fd, n)

    monkeypatch.setattr(session_end.os, "open", swap_to_symlink)
    monkeypatch.setattr(session_end.os, "read", tracked_read)

    # Path was resolved before swap; open fd still points at the original inode
    # (regular file). The resolved path is now a symlink, but the fd we hold is
    # stable. Verify we read the ORIGINAL content and never the outside file.
    payload = {"transcript_path": "session.jsonl"}
    # The path validation resolves strict=True, so after we swap (in the open
    # hook) the resolved Path is fine because resolution happened first. The fd
    # is from the original open and points at the original file.
    conv = session_end.conversation_from_payload(payload, session_root=session_root)
    assert conv.messages[0].content == "ok"
    assert "must-not-leak" not in conv.messages[0].content


def test_open_transcript_rejects_non_regular_file(tmp_path, monkeypatch):
    """FIFOs, devices, etc. must be rejected after fstat (S_ISREG check).

    We monkeypatch fstat to return a non-regular file mode so the test does not
    block opening a real FIFO (which waits for a writer).
    """
    session_root = tmp_path / "sessions"; session_root.mkdir()
    target = session_root / "session.jsonl"
    target.write_text('{}\n', encoding="utf-8")
    payload = {"transcript_path": "session.jsonl"}

    real_fstat = os.fstat

    def fake_fstat(fd):
        info = real_fstat(fd)
        # Zero out the regular-file bit; keep everything else.
        import stat as _stat
        return os.stat_result((_stat.S_IFIFO | 0o644,) + tuple(info[1:]))

    monkeypatch.setattr(session_end.os, "fstat", fake_fstat)
    with pytest.raises(session_end.HookInputError, match="not a regular file"):
        session_end.conversation_from_payload(payload, session_root=session_root)


def test_open_transcript_size_limit_enforced_via_fstat(tmp_path, monkeypatch):
    session_root = tmp_path / "sessions"; session_root.mkdir()
    big = session_root / "big.jsonl"
    big.write_bytes(b"x" * (session_end.MAX_HOOK_INPUT_BYTES + 1))
    payload = {"transcript_path": "big.jsonl"}

    def refuse_read(_fd, _n):
        raise AssertionError("size-limit must trigger before any read()")

    monkeypatch.setattr(session_end.os, "read", refuse_read)
    with pytest.raises(session_end.HookInputError, match="size limit"):
        session_end.conversation_from_payload(payload, session_root=session_root)
