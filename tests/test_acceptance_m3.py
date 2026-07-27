"""M3 acceptance: install, MCP, SessionEnd hook, uninstall.

Deterministic: temp roots, FakeEmbedder, no model/network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from kg.cli.init import init_project
from kg.cli.main import app
from kg.config import Config
from kg.install.manifest import manifest_path
from kg.paths import KgPaths

runner = CliRunner()
SKILLS_SRC = Path(__file__).resolve().parent.parent / "src" / "kg" / "skills"


def _assert_ok(result):
    assert result.exit_code == 0, (
        f"exit={result.exit_code}\nexception={result.exception!r}\n"
        f"output={result.output}\nstderr={result.stderr}"
    )


def _init(project: Path, *, auto_hook: bool = False) -> KgPaths:
    paths = init_project(project, user_id="u", scope="m3-acceptance")
    cfg = Config.from_path(paths.config)
    cfg.dream.auto_hook = auto_hook
    paths.config.write_text(cfg.render_toml(), encoding="utf-8")
    return paths


def _payload(secret: str = "Bearer TOP_SECRET_TOKEN_123") -> dict:
    return {
        "hook_event_name": "SessionEnd",
        "session_id": "session-1",
        "title": "Acceptance transcript",
        "transcript": [
            {"role": "system", "content": "SYSTEM MUST NOT PERSIST"},
            {"role": "developer", "content": "DEVELOPER MUST NOT PERSIST"},
            {"role": "user", "content": f"hello {secret}"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "safe answer"},
                {"type": "tool_use", "input": {"secret": "tool secret"}},
            ]},
        ],
    }


def _rpc(proc, message):
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, (
        f"missing JSON-RPC response\nmessage={message}\n"
        f"returncode={proc.poll()}\nstderr={proc.stderr.read() if proc.stderr else ''}"
    )
    return json.loads(line)


def test_m3_install_mcp_hook_uninstall_roundtrip(tmp_path, monkeypatch):
    """Fresh install, protocol MCP read/write-denial, hook privacy, uninstall."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))

    # Fresh CLI init; dry-run writes neither home config nor project installer state.
    _assert_ok(runner.invoke(app, ["init", "--user-id", "u", "--scope", "m3"]))
    before = sorted(path.relative_to(project) for path in project.rglob("*"))
    dry = runner.invoke(app, ["install", "claude", "--home-root", str(home), "--skills-src", str(SKILLS_SRC)])
    _assert_ok(dry)
    assert "# plan: claude" in dry.output
    assert not (home / ".claude").exists(), f"dry-run wrote home: {list(home.rglob('*'))}"
    assert not manifest_path(project).exists(), f"dry-run wrote manifest: {manifest_path(project)}"
    assert sorted(path.relative_to(project) for path in project.rglob("*")) == before

    applied = runner.invoke(app, ["install", "claude", "--apply", "--home-root", str(home), "--skills-src", str(SKILLS_SRC)])
    _assert_ok(applied)
    claude_json = project / ".mcp.json"
    settings = project / ".claude" / "settings.json"
    instructions = project / "CLAUDE.md"
    assert all((project / ".claude" / "skills" / name / "SKILL.md").is_file()
               for name in ("kg-extract", "kg-query", "kg-dream"))
    assert "kg" in json.loads(claude_json.read_text())["mcpServers"]
    assert json.loads(settings.read_text())["hooks"]["SessionEnd"]
    assert "<!-- kg-install:claude:begin -->" in instructions.read_text()
    installed = {path: path.read_bytes() for path in (claude_json, settings, instructions)}

    reinstall = runner.invoke(app, ["install", "claude", "--apply", "--home-root", str(home), "--skills-src", str(SKILLS_SRC)])
    _assert_ok(reinstall)
    assert "already installed" in reinstall.output
    assert installed == {path: path.read_bytes() for path in installed}

    # Real stdio transport. FakeEmbedder is unnecessary for empty keyword search.
    env = {**os.environ, "HOME": str(home)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "kg", "mcp", "serve", "--project-root", str(project)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env,
    )
    try:
        initialized = _rpc(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "test", "version": "1"}, "capabilities": {}},
        })
        assert initialized["jsonrpc"] == "2.0" and initialized["id"] == 1, initialized
        assert proc.stdin
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        tools = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert any(tool["name"] == "search_memory" for tool in tools["result"]["tools"]), tools
        read = _rpc(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "search_memory", "arguments": {"query": "nothing", "mode": "keyword", "k": 1}},
        })
        assert read["id"] == 3 and "result" in read and not read["result"].get("isError"), read
        denied = _rpc(proc, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "save_pole", "arguments": {"nodes": [], "edges": [], "source": "test"}},
        })
        assert denied["result"]["isError"] is True and "not authorized" in denied["result"]["content"][0]["text"], denied
    finally:
        proc.kill()
        proc.wait(timeout=5)
        stderr = proc.stderr.read() if proc.stderr else ""
    assert not stderr.startswith("{"), f"diagnostic leaked JSON frame to stderr: {stderr}"

    # Hook command: no stdout, redacted storage, hidden roles absent, content-hash dedup.
    cfg = Config.from_path(project / ".kg" / "config.toml")
    cfg.dream.auto_hook = True
    (project / ".kg" / "config.toml").write_text(cfg.render_toml(), encoding="utf-8")
    payload = {**_payload(), "cwd": str(project)}
    hook = subprocess.run(
        [sys.executable, "-m", "kg", "hook", "session-end", "--project-root", str(project)],
        input=json.dumps(payload), capture_output=True, text=True, env={**os.environ, "HOME": str(home)},
    )
    assert hook.returncode == 0, f"exit={hook.returncode}\nstdout={hook.stdout}\nstderr={hook.stderr}"
    assert hook.stdout == "", f"hook stdout leaked: {hook.stdout!r}"
    conversations = list((project / ".kg" / "raw" / "conversations").glob("*.md"))
    assert len(conversations) == 1
    raw = conversations[0].read_text(encoding="utf-8")
    for forbidden in ("TOP_SECRET_TOKEN_123", "SYSTEM MUST NOT PERSIST", "DEVELOPER MUST NOT PERSIST", "tool secret"):
        assert forbidden not in raw
    assert "[REDACTED]" in raw and "safe answer" in raw
    again = subprocess.run(
        [sys.executable, "-m", "kg", "hook", "session-end", "--project-root", str(project)],
        input=json.dumps(payload), capture_output=True, text=True, env={**os.environ, "HOME": str(home)},
    )
    assert again.returncode == 0
    assert again.stdout == ""
    assert len(list((project / ".kg" / "raw" / "conversations").glob("*.md"))) == 1
    committed = [json.loads(line) for line in (project / ".kg" / "logs" / "ingest.jsonl").read_text().splitlines()
                 if json.loads(line)["status"] == "committed"]
    assert len(committed) == 1

    # Opt-out remains a no-op.
    cfg.dream.auto_hook = False
    (project / ".kg" / "config.toml").write_text(cfg.render_toml(), encoding="utf-8")
    opt_out = subprocess.run(
        [sys.executable, "-m", "kg", "hook", "session-end", "--project-root", str(project)],
        input=json.dumps({**payload, "session_id": "session-2"}), capture_output=True, text=True, env={**os.environ, "HOME": str(home)},
    )
    assert opt_out.returncode == 0 and opt_out.stdout == ""
    assert len(list((project / ".kg" / "raw" / "conversations").glob("*.md"))) == 1

    # Preserve user additions while exact kg fragments disappear.
    data = json.loads(claude_json.read_text())
    data["mcpServers"]["user-server"] = {"command": "user"}
    claude_json.write_text(json.dumps(data), encoding="utf-8")
    settings_data = json.loads(settings.read_text())
    settings_data["hooks"]["SessionEnd"].append({"matcher": "user", "hooks": []})
    settings.write_text(json.dumps(settings_data), encoding="utf-8")
    instructions.write_text(instructions.read_text() + "user CLAUDE.md line\n", encoding="utf-8")
    uninstalled = runner.invoke(app, ["install", "claude", "--uninstall", "--home-root", str(home)])
    _assert_ok(uninstalled)
    assert json.loads(claude_json.read_text())["mcpServers"] == {"user-server": {"command": "user"}}
    assert json.loads(settings.read_text())["hooks"]["SessionEnd"] == [{"matcher": "user", "hooks": []}]
    remaining = instructions.read_text()
    assert "kg-install:claude" not in remaining and "user CLAUDE.md line" in remaining
    assert not manifest_path(project).exists()


def test_m3_uninstall_refuses_owned_drift(tmp_path, monkeypatch):
    """Owned changes fail closed; installer leaves all artifacts intact."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(); home.mkdir()
    _init(project)
    installed = runner.invoke(app, ["install", "claude", "--apply", "--project-root", str(project), "--home-root", str(home), "--skills-src", str(SKILLS_SRC)])
    _assert_ok(installed)
    owned = project / ".claude" / "skills" / "kg-query" / "SKILL.md"
    owned.write_text(owned.read_text() + "manual drift\n", encoding="utf-8")
    refused = runner.invoke(app, ["install", "claude", "--uninstall", "--project-root", str(project), "--home-root", str(home)])
    assert refused.exit_code != 0, f"exit={refused.exit_code}\nexception={refused.exception!r}\noutput={refused.output}\nstderr={refused.stderr}"
    diagnostic = " ".join(filter(None, [refused.output, refused.stderr, str(refused.exception)])).lower()
    assert "drift" in diagnostic, f"missing 'drift' in: {diagnostic}"
    assert owned.exists() and manifest_path(project).exists()


def test_m3_agents_dry_apply_uninstall_roundtrip(tmp_path):
    """Non-Claude fallback harness remains dry by default, reversible on apply."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(); home.mkdir()
    _init(project)
    dry = runner.invoke(app, ["install", "agents", "--project-root", str(project), "--home-root", str(home)])
    _assert_ok(dry)
    assert not (project / "AGENTS.md").exists() and not manifest_path(project).exists()
    applied = runner.invoke(app, ["install", "agents", "--apply", "--project-root", str(project), "--home-root", str(home)])
    _assert_ok(applied)
    assert "kg-install:agents:begin" in (project / "AGENTS.md").read_text()
    uninstalled = runner.invoke(app, ["install", "agents", "--uninstall", "--project-root", str(project), "--home-root", str(home)])
    _assert_ok(uninstalled)
    assert not (project / "AGENTS.md").exists() or "kg-install:agents" not in (project / "AGENTS.md").read_text()
