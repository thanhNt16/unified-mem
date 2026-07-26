"""C1: path-mode ingest — CLI --session-root, installer wiring, graceful no-op."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from kg.cli.init import init_project
from kg.cli.main import app
from kg.config import Config
from kg.hooks import session_end
from typer.testing import CliRunner


runner = CliRunner()


def _project(tmp_path: Path, *, enabled: bool = True) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    paths = init_project(root, user_id="u", scope="s")
    cfg = Config.from_path(paths.config)
    cfg.dream.auto_hook = enabled
    paths.config.write_text(cfg.render_toml(), encoding="utf-8")
    return root


def _payload_with_transcript_path(session_root_rel: str, *, session_id="s", cwd: Path | None = None) -> dict:
    payload = {
        "hook_event_name": "SessionEnd",
        "session_id": session_id,
        "transcript_path": session_root_rel,
    }
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return payload


# --- CLI forwards --session-root into run() ---------------------------------

def test_cli_session_end_accepts_session_root_and_ingests(tmp_path):
    root = _project(tmp_path)
    session_root = root / ".kg" / "sessions"
    session_root.mkdir(parents=True)
    transcript = session_root / "session.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "from path"}) + "\n", encoding="utf-8"
    )
    payload = _payload_with_transcript_path("session.jsonl", cwd=root)
    proc = subprocess.run(
        [
            sys.executable, "-m", "kg", "hook", "session-end",
            "--project-root", str(root),
            "--session-root", str(session_root),
        ],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    files = list((root / ".kg/raw/conversations").glob("*.md"))
    assert len(files) == 1
    assert "from path" in files[0].read_text(encoding="utf-8")


def test_cli_session_end_rejects_transcript_path_outside_session_root(tmp_path):
    root = _project(tmp_path)
    session_root = root / ".kg" / "sessions"
    session_root.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{}\n', encoding="utf-8")
    payload = _payload_with_transcript_path(str(outside), cwd=root)
    proc = subprocess.run(
        [
            sys.executable, "-m", "kg", "hook", "session-end",
            "--project-root", str(root),
            "--session-root", str(session_root),
        ],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    assert proc.returncode == 2  # HookInputError
    assert "escapes session root" in proc.stderr or "transcript path escapes" in proc.stderr
    assert not list((root / ".kg/raw/conversations").glob("*.md"))


def test_cli_path_mode_without_session_root_is_graceful_noop(tmp_path):
    """Path-mode payload without --session-root must NOT crash the hook."""
    root = _project(tmp_path)
    payload = _payload_with_transcript_path("session.jsonl", cwd=root)
    proc = subprocess.run(
        [
            sys.executable, "-m", "kg", "hook", "session-end",
            "--project-root", str(root),
        ],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    # graceful no-op: exit 0, no crash, no file written
    assert proc.returncode == 0, proc.stderr
    assert not list((root / ".kg/raw/conversations").glob("*.md"))
    # Should print a clear stderr message; must NOT contain Traceback
    assert "Traceback" not in proc.stderr


# --- Containment: --session-root must live under project root ----------------

def test_cli_session_root_outside_project_rejected(tmp_path):
    root = _project(tmp_path)
    foreign = tmp_path / "foreign-sessions"
    foreign.mkdir()
    (foreign / "session.jsonl").write_text(
        json.dumps({"role": "user", "content": "smuggled"}) + "\n", encoding="utf-8"
    )
    payload = _payload_with_transcript_path("session.jsonl", cwd=root)
    proc = subprocess.run(
        [
            sys.executable, "-m", "kg", "hook", "session-end",
            "--project-root", str(root),
            "--session-root", str(foreign),
        ],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    assert proc.returncode != 0 or "smuggled" not in proc.stdout
    assert "smuggled" not in proc.stderr
    assert not list((root / ".kg/raw/conversations").glob("*.md"))


# --- Direct run() — no CLI parsing ------------------------------------------

def test_run_path_mode_with_trusted_session_root_ingests(tmp_path):
    root = _project(tmp_path)
    session_root = root / ".kg" / "sessions"
    session_root.mkdir(parents=True)
    transcript = session_root / "s.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "direct-path"}) + "\n", encoding="utf-8"
    )
    payload = _payload_with_transcript_path("s.jsonl", cwd=root)
    stderr = io.StringIO()
    code = session_end.run(
        root,
        stdin=io.BytesIO(json.dumps(payload).encode()),
        stderr=stderr,
        session_root=session_root,
    )
    assert code == 0
    files = list((root / ".kg/raw/conversations").glob("*.md"))
    assert len(files) == 1
    assert "direct-path" in files[0].read_text(encoding="utf-8")


def test_run_path_mode_without_session_root_is_noop_no_crash(tmp_path):
    root = _project(tmp_path)
    payload = _payload_with_transcript_path("session.jsonl", cwd=root)
    stderr = io.StringIO()
    code = session_end.run(
        root,
        stdin=io.BytesIO(json.dumps(payload).encode()),
        stderr=stderr,
    )
    assert code == 0
    assert "Traceback" not in stderr.getvalue()
    assert not list((root / ".kg/raw/conversations").glob("*.md"))


def test_run_path_mode_transcript_outside_root_noop_no_read(tmp_path, monkeypatch):
    root = _project(tmp_path)
    session_root = root / ".kg" / "sessions"
    session_root.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{}\n', encoding="utf-8")

    reads = []
    original = Path.read_bytes

    def tracked_read(path):
        reads.append(Path(path))
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read)

    payload = _payload_with_transcript_path(str(outside), cwd=root)
    stderr = io.StringIO()
    code = session_end.run(
        root,
        stdin=io.BytesIO(json.dumps(payload).encode()),
        stderr=stderr,
        session_root=session_root,
    )
    # No file ingested, no read of the outside file content
    assert outside not in reads
    assert not list((root / ".kg/raw/conversations").glob("*.md"))
