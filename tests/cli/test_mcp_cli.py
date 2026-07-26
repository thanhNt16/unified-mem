from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kg.cli.init import init_project
from kg.cli.main import app
from kg.config import Config
from kg.frontmatter import parse as parse_frontmatter
from kg.hooks import session_end
from typer.testing import CliRunner


runner = CliRunner()


def _project(tmp_path: Path, *, enabled: bool = False) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    paths = init_project(root, user_id="u", scope="s")
    cfg = Config.from_path(paths.config)
    cfg.dream.auto_hook = enabled
    paths.config.write_text(cfg.render_toml(), encoding="utf-8")
    return root


def _payload(*, session_id: str = "s-1", title: str = "Title") -> dict:
    return {
        "hook_event_name": "SessionEnd",
        "session_id": session_id,
        "reason": "logout",
        "title": title,
        "transcript": [
            {"role": "system", "content": "hidden"},
            {"role": "user", "content": "hello api_key=SUPERSECRET"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "reply"},
                {"type": "tool_use", "input": {"secret": "hidden-tool"}},
            ]},
            {"role": "tool", "content": "hidden-result"},
        ],
    }


def test_mcp_serve_forwards_read_only_default(tmp_path, monkeypatch):
    root = _project(tmp_path)
    called = {}
    monkeypatch.setattr(
        "kg.mcp.server.run_stdio",
        lambda project_root, *, allow_writes=False: called.update(
            root=project_root, allow_writes=allow_writes
        ),
    )
    result = runner.invoke(app, ["mcp", "serve", "--project-root", str(root)])
    assert result.exit_code == 0, result.output
    assert called == {"root": root, "allow_writes": False}


def test_mcp_serve_allow_writes_opt_in(tmp_path, monkeypatch):
    root = _project(tmp_path)
    called = {}
    monkeypatch.setattr(
        "kg.mcp.server.run_stdio",
        lambda project_root, *, allow_writes=False: called.update(
            root=project_root, allow_writes=allow_writes
        ),
    )
    result = runner.invoke(app, [
        "mcp", "serve", "--project-root", str(root), "--allow-writes",
    ])
    assert result.exit_code == 0, result.output
    assert called["allow_writes"] is True


def test_installed_argv_resolves_commands():
    for argv in (
        ["mcp", "serve", "--project-root", "/tmp/project"],
        ["hook", "session-end", "--project-root", "/tmp/project"],
    ):
        result = runner.invoke(app, [*argv, "--help"])
        assert result.exit_code == 0, result.output


def test_mcp_subprocess_initialize_and_list_tools_protocol_only(tmp_path):
    root = _project(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "kg", "mcp", "serve", "--project-root", str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "pytest", "version": "0"},
                "capabilities": {},
            },
        }) + "\n")
        proc.stdin.flush()
        initialized = json.loads(proc.stdout.readline())
        assert initialized["id"] == 1
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }) + "\n")
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        }) + "\n")
        proc.stdin.flush()
        tools = json.loads(proc.stdout.readline())
        assert tools["id"] == 2
        assert any(t["name"] == "search_memory" for t in tools["result"]["tools"])
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_hook_valid_redacts_hidden_roles_and_stdout_empty(tmp_path):
    root = _project(tmp_path, enabled=True)
    stdin = io.BytesIO(json.dumps(_payload()).encode())
    stderr = io.StringIO()
    code = session_end.run(root, stdin=stdin, stderr=stderr)
    assert code == 0
    files = list((root / ".kg/raw/conversations").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "hello" in text and "reply" in text
    assert "SUPERSECRET" not in text
    assert "hidden" not in text and "hidden-tool" not in text and "hidden-result" not in text
    assert "conversation ingested" in stderr.getvalue()


def test_hook_cli_stdout_always_empty_and_stderr_redacted(tmp_path):
    root = _project(tmp_path, enabled=True)
    secret = "NEVER-PRINT-ME"
    payload = _payload()
    payload["transcript"][1]["content"] = f"api_key={secret}"
    proc = subprocess.run(
        [sys.executable, "-m", "kg", "hook", "session-end", "--project-root", str(root)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert secret not in proc.stderr


@pytest.mark.parametrize("raw", [
    b"{bad",
    b"[]",
    json.dumps({"hook_event_name": "FutureEvent", "session_id": "s"}).encode(),
    json.dumps({"hook_event_name": "SessionEnd", "session_id": "s", "version": 99}).encode(),
])
def test_hook_rejects_malformed_array_unknown_event_or_version(tmp_path, raw):
    root = _project(tmp_path, enabled=True)
    stderr = io.StringIO()
    assert session_end.run(root, stdin=io.BytesIO(raw), stderr=stderr) == 2
    assert list((root / ".kg/raw/conversations").glob("*.md")) == []


def test_hook_rejects_oversized_input(tmp_path):
    root = _project(tmp_path, enabled=True)
    raw = b"{" + b"x" * session_end.MAX_HOOK_INPUT_BYTES + b"}"
    assert session_end.run(root, stdin=io.BytesIO(raw), stderr=io.StringIO()) == 2


def test_hook_opt_out_is_noop_without_transcript_read(tmp_path):
    root = _project(tmp_path, enabled=False)
    payload = {
        "hook_event_name": "SessionEnd", "session_id": "s",
        "transcript_path": "../../private",
    }
    stderr = io.StringIO()
    assert session_end.run(
        root, stdin=io.BytesIO(json.dumps(payload).encode()), stderr=stderr
    ) == 0
    assert "disabled" in stderr.getvalue()
    assert not (root / ".kg/logs/ingest.jsonl").exists()


def test_hook_idempotent_twice_one_record(tmp_path):
    root = _project(tmp_path, enabled=True)
    payload = _payload()
    for _ in range(2):
        assert session_end.run(
            root, stdin=io.BytesIO(json.dumps(payload).encode()), stderr=io.StringIO()
        ) == 0
    assert len(list((root / ".kg/raw/conversations").glob("*.md"))) == 1
    committed = [
        json.loads(line) for line in (root / ".kg/logs/ingest.jsonl").read_text().splitlines()
        if json.loads(line)["status"] == "committed"
    ]
    assert len(committed) == 1


def test_hook_same_title_different_content_two_files(tmp_path):
    root = _project(tmp_path, enabled=True)
    first = _payload(session_id="s1", title="same")
    second = _payload(session_id="s2", title="same")
    second["transcript"][1]["content"] = "different"
    for payload in (first, second):
        assert session_end.run(
            root, stdin=io.BytesIO(json.dumps(payload).encode()), stderr=io.StringIO()
        ) == 0
    files = list((root / ".kg/raw/conversations").glob("*.md"))
    assert len(files) == 2
    assert len({file.name for file in files}) == 2


def test_hook_nested_jsonl_fixture(tmp_path):
    root = _project(tmp_path, enabled=True)
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    transcript = session_root / "session.jsonl"
    transcript.write_text("\n".join([
        json.dumps({"type": "system", "message": {"role": "system", "content": "hidden"}}),
        "{malformed",
        json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "visible"}]}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}, {"type": "tool_use", "input": {"x": 1}}]}}),
    ]), encoding="utf-8")
    payload = {
        "hook_event_name": "SessionEnd", "session_id": "s",
        "transcript_path": "session.jsonl",
    }
    conv = session_end.conversation_from_payload(payload, session_root=session_root)
    assert [m.content for m in conv.messages] == ["visible", "answer"]
    assert conv.metadata.malformed == 1


@pytest.mark.parametrize("path", ["../escape.jsonl", "https://example.com/x", "/etc/passwd"])
def test_hook_transcript_path_traversal_url_absolute_rejected(tmp_path, path):
    root = _project(tmp_path, enabled=True)
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    payload = {
        "hook_event_name": "SessionEnd", "session_id": "s", "transcript_path": path,
    }
    with pytest.raises(session_end.HookInputError):
        session_end.conversation_from_payload(payload, session_root=session_root)


def test_hook_transcript_absolute_path_inside_session_root_allowed(tmp_path):
    root = _project(tmp_path, enabled=True)
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    transcript = session_root / "session.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "ok"}) + "\n", encoding="utf-8")
    payload = {
        "hook_event_name": "SessionEnd", "session_id": "s",
        "transcript_path": str(transcript),
    }
    conv = session_end.conversation_from_payload(payload, session_root=session_root)
    assert [m.content for m in conv.messages] == ["ok"]


def test_hook_transcript_absolute_path_outside_session_root_rejected(tmp_path):
    root = _project(tmp_path, enabled=True)
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{}\n', encoding="utf-8")
    payload = {
        "hook_event_name": "SessionEnd", "session_id": "s",
        "transcript_path": str(outside),
    }
    with pytest.raises(session_end.HookInputError):
        session_end.conversation_from_payload(payload, session_root=session_root)


def test_hook_transcript_symlink_rejected(tmp_path):
    root = _project(tmp_path, enabled=True)
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{}\n')
    (session_root / "link.jsonl").symlink_to(outside)
    payload = {
        "hook_event_name": "SessionEnd", "session_id": "s",
        "transcript_path": "link.jsonl",
    }
    with pytest.raises(session_end.HookInputError):
        session_end.conversation_from_payload(payload, session_root=session_root)


def test_hook_repairs_registry_after_raw_registry_failure(tmp_path, monkeypatch):
    root = _project(tmp_path, enabled=True)
    payload = _payload()
    original = session_end.Registry.append
    failed = False

    def fail_once(self, entry):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected")
        return original(self, entry)

    monkeypatch.setattr(session_end.Registry, "append", fail_once)
    assert session_end.run(
        root, stdin=io.BytesIO(json.dumps(payload).encode()), stderr=io.StringIO()
    ) == 1
    assert len(list((root / ".kg/raw/conversations").glob("*.md"))) == 1
    assert session_end.run(
        root, stdin=io.BytesIO(json.dumps(payload).encode()), stderr=io.StringIO()
    ) == 0
    assert len(list((root / ".kg/raw/conversations").glob("*.md"))) == 1
    assert len((root / ".kg/registry.jsonl").read_text().splitlines()) == 1
