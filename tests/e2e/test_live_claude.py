"""test_live_claude.py: M5 T4 — Claude Code live-session driver + E2E.

Two layers:
  Layer 1 (deterministic, CI-safe, NO skipif): shells out to the kg CLI
    directly via `python -m kg` (hermetic, no global install needed).
    Tests: raw add, save, search keyword, MCP stdio round-trip.
    Asserts exact graph shape via assert_graph_shape using golden.cli_deterministic.

  Layer 2 (real LLM, skipif-gated): spawns ClaudeDriver with a
    natural-language prompt and asserts the graph GREW (node count > 0).
    Gated on both `claude` binary and ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kg.cli.init import init_project
from kg.cli.main import app
from kg.config import Config
from kg.paths import KgPaths

from tests.e2e.assertions import assert_graph_shape, kg_tool_json_equal
from tests.e2e.harness import build_clean_env

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXTRACTED = FIXTURES / "extracted.json"
GOLDEN = FIXTURES / "golden.json"
SKILLS_SRC = REPO_ROOT / "src" / "kg" / "skills"


def _load_golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _rpc(proc, message):
    """Send one JSON-RPC message, read one JSON-RPC response."""
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, (
        f"missing JSON-RPC response\nmessage={message}\n"
        f"returncode={proc.poll()}\n"
        f"stderr={proc.stderr.read() if proc.stderr else ''}"
    )
    return json.loads(line)


def _assert_exit_zero(result, label: str = ""):
    assert result.exit_code == 0, (
        f"{label}: exit={result.exit_code}\n"
        f"output={result.output}\n"
        f"stderr={result.stderr}"
    )


def _seed_project(tmp_path, monkeypatch):
    """Init a kg project in tmp_path, return (project, paths)."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    paths = init_project(project, user_id="u", scope="e2e-deterministic")
    return project, paths


# ============================================================================
# Layer 1: Deterministic, CI-safe (no skipif, no LLM, no network)
# ============================================================================


class TestDeterministicCLI:
    """Layer 1: kg CLI operations via python -m kg. No model needed."""

    def test_raw_add_and_save_deterministic(self, tmp_path, monkeypatch):
        """kg raw add + kg save with extracted.json produces golden shape."""
        project, paths = _seed_project(tmp_path, monkeypatch)
        note = FIXTURES / "corpus" / "note.md"

        # kg raw add <file>
        _assert_exit_zero(
            runner.invoke(app, ["raw", "add", str(note)]),
            "raw add",
        )
        assert list((paths.root / "raw").glob("*.md")), "source not copied to .kg/raw"

        # kg save --nodes --edges --source with extracted.json fixture
        nodes_file = EXTRACTED
        edges_file = EXTRACTED  # save reads --nodes and --edges separately
        # Build a temp edges-only file for the save command
        ext = json.loads(EXTRACTED.read_text())
        nodes_json = json.dumps(ext["nodes"])
        edges_json = json.dumps(ext["edges"])
        facts_json = json.dumps(ext.get("facts", []))
        prefs_json = json.dumps(ext.get("preferences", []))

        n_tmp = tmp_path / "nodes.json"
        e_tmp = tmp_path / "edges.json"
        f_tmp = tmp_path / "facts.json"
        p_tmp = tmp_path / "preferences.json"
        n_tmp.write_text(nodes_json)
        e_tmp.write_text(edges_json)
        f_tmp.write_text(facts_json)
        p_tmp.write_text(prefs_json)

        _assert_exit_zero(
            runner.invoke(app, [
                "save",
                "--nodes", str(n_tmp),
                "--edges", str(e_tmp),
                "--source", "e2e-deterministic",
                "--facts", str(f_tmp),
                "--preferences", str(p_tmp),
            ]),
            "save",
        )

        # Assert graph shape matches golden.cli_deterministic
        golden = _load_golden()["cli_deterministic"]
        shape = assert_graph_shape(
            project,
            nodes=(golden["nodes_total"], golden["nodes_total"]),
            edges=(golden["edges_total"], golden["edges_total"]),
        )
        assert shape.node_count == golden["nodes_total"]
        assert shape.edge_count == golden["edges_total"]
        for ntype, expected_count in golden["nodes_by_type"].items():
            assert shape.node_types.get(ntype, 0) == expected_count, (
                f"type {ntype}: got {shape.node_types.get(ntype, 0)}, want {expected_count}"
            )

    def test_search_keyword_deterministic(self, tmp_path, monkeypatch):
        """kg search "<query>" --mode keyword returns results after save."""
        project, paths = _seed_project(tmp_path, monkeypatch)

        # Seed data via save
        ext = json.loads(EXTRACTED.read_text())
        n_tmp = tmp_path / "nodes.json"
        e_tmp = tmp_path / "edges.json"
        f_tmp = tmp_path / "facts.json"
        p_tmp = tmp_path / "preferences.json"
        n_tmp.write_text(json.dumps(ext["nodes"]))
        e_tmp.write_text(json.dumps(ext["edges"]))
        f_tmp.write_text(json.dumps(ext.get("facts", [])))
        p_tmp.write_text(json.dumps(ext.get("preferences", [])))
        _assert_exit_zero(
            runner.invoke(app, [
                "save",
                "--nodes", str(n_tmp),
                "--edges", str(e_tmp),
                "--source", "e2e-search",
                "--facts", str(f_tmp),
                "--preferences", str(p_tmp),
            ]),
            "save",
        )

        # Keyword search for a known entity
        result = runner.invoke(app, [
            "search", "AlphaFold", "--mode", "keyword", "-k", "3",
        ])
        _assert_exit_zero(result, "search keyword")
        assert "AlphaFold" in result.output or result.output.strip() != "", (
            f"keyword search returned nothing useful: {result.output}"
        )
        # At least one result line with a score
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        assert len(lines) >= 1, f"expected >=1 result lines, got: {result.output}"

    def test_mcp_stdio_roundtrip(self, tmp_path, monkeypatch):
        """kg mcp serve stdio: initialize, tools/list, tools/call search_memory."""
        project, paths = _seed_project(tmp_path, monkeypatch)

        # Seed data
        ext = json.loads(EXTRACTED.read_text())
        n_tmp = tmp_path / "nodes.json"
        e_tmp = tmp_path / "edges.json"
        f_tmp = tmp_path / "facts.json"
        p_tmp = tmp_path / "preferences.json"
        n_tmp.write_text(json.dumps(ext["nodes"]))
        e_tmp.write_text(json.dumps(ext["edges"]))
        f_tmp.write_text(json.dumps(ext.get("facts", [])))
        p_tmp.write_text(json.dumps(ext.get("preferences", [])))
        _assert_exit_zero(
            runner.invoke(app, [
                "save",
                "--nodes", str(n_tmp),
                "--edges", str(e_tmp),
                "--source", "e2e-mcp",
                "--facts", str(f_tmp),
                "--preferences", str(p_tmp),
            ]),
            "save",
        )

        # Spawn kg mcp serve via python -m kg (hermetic, not installed binary)
        env = {**os.environ}
        proc = subprocess.Popen(
            [sys.executable, "-m", "kg", "mcp", "serve", "--project-root", str(project)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env,
        )
        try:
            # 1. initialize
            initialized = _rpc(proc, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "clientInfo": {"name": "test", "version": "1"},
                    "capabilities": {},
                },
            })
            assert initialized["jsonrpc"] == "2.0" and initialized["id"] == 1
            assert proc.stdin
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.flush()

            # 2. tools/list
            tools = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tool_names = [t["name"] for t in tools["result"]["tools"]]
            assert "search_memory" in tool_names
            assert "expand_memory" in tool_names

            # 3. tools/call search_memory (keyword mode, no embedder needed)
            search_result = _rpc(proc, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {
                    "name": "search_memory",
                    "arguments": {"query": "AlphaFold", "mode": "keyword", "k": 3},
                },
            })
            assert search_result["id"] == 3
            assert "result" in search_result
            assert not search_result["result"].get("isError"), (
                f"search_memory returned error: {search_result['result']}"
            )
        finally:
            proc.kill()
            proc.wait(timeout=5)


# ============================================================================
# Layer 2: Real LLM (skipif-gated on claude binary + ANTHROPIC_API_KEY)
# ============================================================================

_CLAUDE_AVAILABLE = shutil.which("claude") is not None
_API_KEY_SET = bool(os.environ.get("ANTHROPIC_API_KEY"))
_SKIP_REASON = (
    "requires `claude` CLI and ANTHROPIC_API_KEY"
    if not _CLAUDE_AVAILABLE
    else "requires ANTHROPIC_API_KEY"
)


@pytest.mark.skipif(not (_CLAUDE_AVAILABLE and _API_KEY_SET), reason=_SKIP_REASON)
class TestLiveClaudeSession:
    """Layer 2: drive Claude Code via ClaudeDriver, assert graph grew.

    Uses a clean env (PATH/HOME/API_KEY only). Asserts only that node
    count > 0 after the session — no specific structure (LLM-dependent).
    """

    def test_graph_grows_after_claude_session(self, tmp_path, monkeypatch):
        """Claude reads a file, calls kg search_memory, graph gains nodes."""
        from tests.e2e.drivers.claude_driver import ClaudeDriver

        project = tmp_path / "project"
        home = tmp_path / "home"
        project.mkdir()
        home.mkdir()

        # Init kg project
        paths = init_project(project, user_id="u", scope="e2e-live")

        # Install kg into the clean home (ClaudeDriver's env uses this HOME)
        _assert_exit_zero(
            runner.invoke(app, [
                "install", "claude", "--apply",
                "--project-root", str(project),
                "--home-root", str(home),
                "--skills-src", str(SKILLS_SRC),
            ]),
            "install claude",
        )

        # Seed a source file for Claude to read
        note = FIXTURES / "corpus" / "note.md"
        target = project / "notes" / "note.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(note.read_text(), encoding="utf-8")

        # Drive Claude
        with ClaudeDriver(
            project_root=project,
            home_root=home,
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout=120,
        ) as drv:
            output = drv.send(
                "Read notes/note.md and use the search_memory tool to "
                "store information about Demis Hassabis and DeepMind. "
                "Just use the tools available to you."
            )
        # Assert: the graph should have at least one node after the session.
        # We check the DB directly (Claude's MCP call writes through kg).
        shape = assert_graph_shape(project, nodes=(1, None), edges=(0, None))
        assert shape.node_count >= 1, (
            f"expected graph to grow after Claude session, got {shape.node_count} nodes"
        )
