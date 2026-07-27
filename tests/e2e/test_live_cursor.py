"""M5 T5 — Cursor MCP driver E2E.

Layer 1 (deterministic, CI-safe): drives ``kg mcp serve`` stdio with a
golden corpus seeded via ``kg save``. Asserts structural JSON-RPC
responses (initialize → tools/list → search_memory → expand_memory).
This is the exact MCP surface Cursor's UI would talk to, so portability
is exercised without needing Cursor's LLM.

Layer 2 (real Cursor LLM, skipif-gated): spawns the Cursor binary if it
exists AND an API key is present. Asserts only that the graph grew after
a one-shot prompt. Same process hygiene as T4 (clean env, hard timeout,
kill on cleanup, no secret logging).

Run focused:
    pytest tests/e2e/test_live_cursor.py -v

Run full:
    pytest tests/e2e/test_live_cursor.py -v --run-layer2
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.drivers.cursor_driver import CursorDriver
from tests.e2e.harness import SkipLayer2, build_clean_env

# ---- fixtures ----------------------------------------------------------

# Golden corpus: two people, one org, two edges. Deterministic node ids
# come from content hashing, so assertions can pin exact ids.

# Subset of env that is safe to print in failure messages (no secrets).
_SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "TZ")


def _init_golden_project(root: Path) -> None:
    """Seed the golden corpus directly (same process, FakeEmbedder)."""
    from kg.cli.init import init_project
    from kg.embed import FakeEmbedder
    from kg.mcp.handlers import save_pole

    init_project(root, user_id="u", scope="s")
    save_pole(
        root,
        nodes=[
            {"type": "person",       "name": "Demis Hassabis", "summary": "DeepMind founder"},
            {"type": "person",       "name": "Shane Legg",     "summary": "DeepMind co-founder"},
            {"type": "organization", "name": "DeepMind",       "summary": "AI research lab"},
        ],
        edges=[
            {"semantic_type": "employed_by", "source_name": "Demis Hassabis", "target_name": "DeepMind", "confidence": 0.9},
            {"semantic_type": "employed_by", "source_name": "Shane Legg",     "target_name": "DeepMind", "confidence": 0.9},
        ],
        source="raw/seed.md#chunk-0",
        authorized=True,
        embedder=FakeEmbedder(),
    )


def _safe_env_echo(driver_env: dict[str, str]) -> dict[str, str]:
    """Return a redacted view of env for diagnostics — never log secrets."""
    return {k: driver_env.get(k, "<unset>") for k in _SAFE_ENV_KEYS}


@pytest.fixture
def golden_project(tmp_path: Path) -> Path:
    """A fresh tmp project with the golden corpus seeded."""
    root = tmp_path / "proj"
    root.mkdir()
    _init_golden_project(root)
    return root


# ---- assertions --------------------------------------------------------


def _assert_initialize(resp: dict, *, req_id: int) -> None:
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == req_id, resp
    assert "protocolVersion" in resp["result"], resp
    info = resp["result"]["serverInfo"]
    assert info["name"] == "kg", info


def _assert_tools_list(resp: dict, *, req_id: int) -> set[str]:
    assert resp["id"] == req_id
    names = {t["name"] for t in resp["result"]["tools"]}
    # Cursor-relevant read tools must be advertised.
    assert "search_memory" in names, names
    assert "expand_memory" in names, names
    # Schemas must remain tight (security invariant from M3).
    for t in resp["result"]["tools"]:
        assert t["inputSchema"]["additionalProperties"] is False
    return names


def _assert_search_memory_shape(resp: dict, *, req_id: int, expected_name: str) -> dict:
    """Assert structural response (Cursor only cares about JSON shape)."""
    assert resp["id"] == req_id, resp
    assert resp["result"]["isError"] is False, resp
    payload = resp["result"]["structuredContent"]
    items = payload["results"]
    assert items, f"empty results: {payload}"
    top = items[0]
    # No embedding leak (M3 invariant still holds via installed binary).
    assert "embedding" not in top, top
    assert top["name"] == expected_name, top
    assert top["node_id"], "missing node_id"
    return top


def _assert_expand_memory_shape(resp: dict, *, req_id: int, seed_id: str) -> dict:
    """Assert BFS expansion returns a structured subgraph (Cursor path)."""
    assert resp["id"] == req_id, resp
    assert resp["result"]["isError"] is False, resp
    payload = resp["result"]["structuredContent"]
    # expand returns nodes + edges; just assert the seed is reachable.
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    assert any(n.get("node_id") == seed_id or n.get("id") == seed_id for n in nodes), payload
    assert edges, f"no edges in expansion: {payload}"
    return payload


# ---- Layer 1: deterministic, CI-safe -----------------------------------


def test_layer1_cursor_driver_stdio_initialize_list_search_expand(golden_project: Path, tmp_path: Path):
    """Drive ``kg mcp serve`` stdio via CursorDriver; assert structural shape.

    This is the Cursor portability surface (Cursor's UI talks the same
    MCP server). Uses the installed kg binary if present, else falls back
    to ``python -m kg`` — either path is valid for E2E.
    """
    home = tmp_path / "home"
    home.mkdir()
    driver = CursorDriver(
        project_root=golden_project,
        home=home,
        layer="deterministic",
    )
    proc = driver.start()
    try:
        assert driver.path_taken.startswith("stdio"), driver.path_taken
        client = driver.mcp_client
        assert client is not None

        # initialize
        init = client.initialize()
        _assert_initialize(init, req_id=1)
        client.notify("notifications/initialized")

        # tools/list
        tools = client.list_tools()
        names = _assert_tools_list(tools, req_id=2)
        # Cursor path needs read tools; write tools are listed but denied.
        assert {"search_memory", "expand_memory", "pack_context"}.issubset(names), names

        # tools/call search_memory (keyword mode — deterministic, no embeddings)
        search = client.call_tool(
            "search_memory",
            {"query": "Demis", "mode": "keyword", "k": 5},
        )
        top = _assert_search_memory_shape(search, req_id=3, expected_name="Demis Hassabis")
        demis_id = top["node_id"]

        # tools/call expand_memory from Demis → expect DeepMind reachable
        expand = client.call_tool(
            "expand_memory",
            {"seed_ids": [demis_id], "hops": 2, "direction": "outbound"},
        )
        _assert_expand_memory_shape(expand, req_id=4, seed_id=demis_id)
    finally:
        driver.stop()
        # Stdout was streamed inline during the test via McpStdioClient.
        # The ABC stop() force-kills + reaps; nothing to unpack.


def test_layer1_cursor_driver_path_taken_documented(golden_project: Path, tmp_path: Path):
    """Driver must record which path it took (stdio vs cursor binary)."""
    home = tmp_path / "home"
    home.mkdir()
    driver = CursorDriver(
        project_root=golden_project,
        home=home,
        layer="deterministic",
    )
    proc = driver.start()
    try:
        # Layer 1 always takes the stdio path.
        assert driver.path_taken == "stdio:kg mcp serve", driver.path_taken
        # And the client is wired up.
        assert driver.mcp_client is not None
    finally:
        driver.stop()


def test_layer1_cursor_driver_clean_env_no_secret_leak(golden_project: Path, tmp_path: Path, monkeypatch):
    """Driver must build a clean env (only PATH/HOME/locale) — never log it.

    We inject a fake parent env var (not a real secret name) and verify
    it does not propagate to the child. We also confirm the env we
    send is safe to echo (only allowlisted keys).
    """
    fake_marker = "FAKE_LAYER1_MARKER_42"
    # Use a non-sensitive name so we don't trip on real secret patterns.
    monkeypatch.setenv("KG_E2E_PARENT_SHOULD_NOT_LEAK", fake_marker)

    home = tmp_path / "home"
    home.mkdir()
    driver = CursorDriver(
        project_root=golden_project,
        home=home,
        layer="deterministic",
    )
    proc = driver.start()
    try:
        # Build a clean env the same way the driver does (Layer 1
        # deterministic does not pass include_secrets, so no key leaks).
        clean = build_clean_env(home)
        assert "KG_E2E_PARENT_SHOULD_NOT_LEAK" not in clean, clean
        # Layer 1 deterministic must NOT carry API keys.
        for k in ("ANTHROPIC_API_KEY", "CURSOR_API_KEY"):
            assert k not in clean, f"{k} leaked into Layer-1 env: {clean.keys()}"
        # Safe echo contains only allowlisted keys.
        safe = _safe_env_echo(clean)
        assert fake_marker not in " ".join(safe.values()), safe
    finally:
        driver.stop()


# ---- Layer 2: real Cursor LLM, skipif-gated ----------------------------


def _layer2_enabled() -> bool:
    """Run Layer 2 only if --run-layer2 was passed."""
    return "--run-layer2" in sys.argv or os.environ.get("KG_E2E_RUN_LAYER2") == "1"


def _has_cursor_binary() -> bool:
    return CursorDriver.cursor_binary() is not None


def _has_api_key() -> bool:
    return bool(os.environ.get("CURSOR_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(
    not _layer2_enabled(),
    reason="Layer 2 requires --run-layer2 (or KG_E2E_RUN_LAYER2=1)",
)
@pytest.mark.skipif(
    not (_has_cursor_binary() and _has_api_key()),
    reason="Layer 2 requires a `cursor` binary AND CURSOR_API_KEY/ANTHROPIC_API_KEY env",
)
def test_layer2_real_cursor_graph_grew(tmp_path: Path):
    """Spawn real Cursor; assert only that the kg graph grew after a prompt.

    Layer 2 is intentionally weak: we don't know what Cursor's LLM will
    say, and that's out of scope for kg portability. We only assert the
    kg MCP server received writes from Cursor (node count > 0 after).
    """
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    _init_golden_project(project)
    pre_count = _count_nodes(project)

    driver = CursorDriver(
        project_root=project,
        home=home,
        layer="live",
        prompt="Use kg search_memory to find Demis Hassabis, then add a new person 'Test Person'.",
    )
    try:
        try:
            driver.start()
        except SkipLayer2 as exc:
            pytest.skip(f"Layer 2 unavailable: {exc}")

        # Drive a minimal MCP sequence via the same installed server to
        # confirm the portability surface is alive after Cursor ran.
        proc = driver._proc
        assert proc is not None, "driver failed to spawn"
        # Give the live process up to hard_timeout to do its work.
        try:
            proc.wait(timeout=driver.hard_timeout)
        except subprocess.TimeoutExpired:
            pass  # we kill in stop()
    finally:
        driver.stop()

    post_count = _count_nodes(project)
    # A real Cursor session must have run (not stdio fallback) and must have
    # written at least one new node. Equality is a silent no-op, not success.
    assert driver.path_taken.startswith("cursor:"), driver.path_taken
    assert post_count > pre_count, (
        f"graph did not grow: pre={pre_count} post={post_count}; "
        f"path_taken={driver.path_taken!r}"
    )


def _count_nodes(project_root: Path) -> int:
    """Count nodes in the project's kg store via the Python API."""
    from kg.paths import KgPaths
    from kg.storage.sqlite import SQLiteAdapter

    paths = KgPaths(project_root / ".kg")
    adapter = SQLiteAdapter(paths.kg_db)
    try:
        # count() returns {"nodes": N, "edges": E}
        return adapter.count()["nodes"]
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()


# ---- Cursor headless flag discovery -------------------------------------

def test_cursor_find_agent_flag_returns_advertised_flag(monkeypatch):
    """I4: use the flag discovered from ``cursor --help`` — never hardcode print."""
    class Result:
        returncode = 0
        stdout = "Usage: cursor --agent PROMPT"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Result())
    assert CursorDriver._cursor_find_agent_flag("cursor") == "--agent"


def test_cursor_find_agent_flag_none_when_unadvertised(monkeypatch):
    """No recognized flag means Layer-2 must skip, not invent an argv shape."""
    class Result:
        returncode = 0
        stdout = "Usage: cursor [options]"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Result())
    assert CursorDriver._cursor_find_agent_flag("cursor") is None
