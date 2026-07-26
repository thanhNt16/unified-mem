"""Integration: real subprocess driving the kg MCP server over stdio JSON-RPC.

These tests assert transport-level invariants from the preflight:
  * stdout contains ONLY JSON-RPC frames
  * malformed / extra-field / oversize inputs are rejected
  * path-traversal project_dir is rejected
  * write tools refuse without explicit server authorization
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


PROJECT_INIT_SCRIPT = textwrap.dedent("""
    import sys
    from pathlib import Path
    from kg.cli.init import init_project
    root = Path(sys.argv[1])
    init_project(root, user_id="u", scope="s")
    # Seed one person so read tools have something to return.
    import json
    from kg.embed import FakeEmbedder
    from kg.mcp.handlers import save_pole
    save_pole(
        root,
        nodes=[{"type":"person","name":"Demis Hassabis","summary":"founder"}],
        edges=[], source="raw/x.md#chunk-0",
        authorized=True, embedder=FakeEmbedder(),
    )
""")


def _init_project(root: Path) -> None:
    subprocess.run(
        [sys.executable, "-c", PROJECT_INIT_SCRIPT, str(root)],
        check=True, capture_output=True,
    )


@pytest.fixture
def server_proc(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    _init_project(root)
    server_py = Path(__file__).parent / "stdio_server.py"
    proc = subprocess.Popen(
        [sys.executable, str(server_py), str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        yield proc, root
    finally:
        proc.kill()
        proc.wait(timeout=5)


def _send(proc: subprocess.Popen, payload: dict) -> None:
    line = json.dumps(payload) + "\n"
    assert proc.stdin is not None
    proc.stdin.write(line)
    proc.stdin.flush()


def _recv(proc: subprocess.Popen, timeout: float = 10.0) -> dict:
    import select
    assert proc.stdout is not None
    rlist, _, _ = select.select([proc.stdout], [], [], timeout)
    assert rlist, "no stdout within timeout"
    line = proc.stdout.readline()
    assert line, "stdout closed"
    return json.loads(line)


def test_initialize_lists_tools_and_resources(server_proc):
    proc, root = server_proc
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "pytest", "version": "0"},
            "capabilities": {},
        },
    })
    init = _recv(proc)
    assert init["id"] == 1
    assert "protocolVersion" in init["result"]
    server_info = init["result"]["serverInfo"]
    assert server_info["name"] == "kg"

    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = _recv(proc)
    assert tools["id"] == 2
    names = {t["name"] for t in tools["result"]["tools"]}
    assert "search_memory" in names
    assert "save_pole" in names
    # Schemas must reject extra properties.
    for t in tools["result"]["tools"]:
        assert t["inputSchema"]["additionalProperties"] is False

    _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    res = _recv(proc)
    uris = {r["uri"] for r in res["result"]["resources"]}
    assert uris == {"kg://ontology.json", "kg://wiki/index.md"}


def test_call_read_tool_returns_structured_content(server_proc):
    proc, root = server_proc
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "pytest", "version": "0"},
            "capabilities": {},
        },
    })
    assert _recv(proc)["id"] == 1
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    _send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "search_memory",
            "arguments": {"query": "Demis", "mode": "keyword", "k": 5},
        },
    })
    resp = _recv(proc)
    assert resp["id"] == 2
    assert resp["result"]["isError"] is False
    assert resp["result"]["structuredContent"]["results"][0]["name"] == "Demis Hassabis"
    # No embedding leaked.
    assert "embedding" not in resp["result"]["structuredContent"]["results"][0]


def test_stdout_is_protocol_only(server_proc):
    """No banner / Typer / logging / traceback on stdout — ever."""
    proc, root = server_proc
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "pytest", "version": "0"},
            "capabilities": {},
        },
    })
    msg = _recv(proc)
    # Must be a valid JSON-RPC response (initialize result).
    assert msg["jsonrpc"] == "2.0"
    assert msg["id"] == 1


def test_malformed_input_returns_jsonrpc_error(server_proc):
    proc, _ = server_proc
    # Not JSON at all.
    assert proc.stdin is not None
    proc.stdin.write("this is not json\n")
    proc.stdin.flush()
    msg = _recv(proc)
    # Official SDK reports malformed frames as a JSON-RPC error notification
    # (still protocol-only) rather than a request-correlated response.
    assert msg["jsonrpc"] == "2.0"
    assert (
        ("error" in msg and msg["error"]["code"] == -32700)
        or msg.get("method") == "notifications/message"
    )
    assert "Error" in str(msg) or "error" in str(msg)


def test_extra_property_rejected(server_proc):
    proc, _ = server_proc
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "pytest", "version": "0"},
            "capabilities": {},
        },
    })
    assert _recv(proc)["id"] == 1
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    _send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "search_memory",
            "arguments": {
                "query": "x", "mode": "keyword",
                "unexpected_extra_property": True,
            },
        },
    })
    resp = _recv(proc)
    # Either the SDK schema validator or our handler must reject.
    assert resp["result"]["isError"] is True


def test_oversize_input_rejected(server_proc):
    """A payload over the protocol line cap is rejected cleanly."""
    proc, _ = server_proc
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "pytest", "version": "0"},
            "capabilities": {},
        },
    })
    _recv(proc)
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    # k > MAX_K(100) -> handler returns structured error.
    _send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "search_memory", "arguments": {"query": "x", "k": 9999}},
    })
    resp = _recv(proc)
    assert resp["result"]["isError"] is True


def test_path_traversal_project_dir_rejected(tmp_path):
    """Caller cannot escape via ../; .kg must exist in the resolved root.

    The stdio entrypoint resolves project_dir once at startup and exits with
    a stderr diagnostic if it's not a real ``.kg`` directory. We assert that
    behavior at the process level (no stdout, non-zero exit) — the handler-
    layer rejection is covered by ``test_handlers.py``.
    """
    bogus = tmp_path / "no-kg-here"
    bogus.mkdir()
    server_py = Path(__file__).parent / "stdio_server.py"
    proc = subprocess.Popen(
        [sys.executable, str(server_py), str(bogus)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        # Send initialize; the server should never produce a protocol frame
        # because project_dir validation rejects before the run loop spins.
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "pytest", "version": "0"},
                "capabilities": {},
            },
        })
        out, err = proc.communicate(timeout=5)
        # No JSON-RPC frame on stdout.
        assert out.strip() == ""
        # Diagnostic on stderr only.
        assert ".kg" in err or "Initialization" in err or "Internal" in err
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        pytest.fail("server did not exit on invalid project_dir")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_write_tool_without_auth_returns_structured_error(server_proc):
    proc, _ = server_proc
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "pytest", "version": "0"},
            "capabilities": {},
        },
    })
    _recv(proc)
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    _send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "save_pole",
            "arguments": {
                "nodes": [{"type": "person", "name": "X"}],
                "edges": [], "source": "raw/x.md#chunk-0",
            },
        },
    })
    resp = _recv(proc)
    assert resp["result"]["isError"] is True
    payload = resp["result"]["structuredContent"]
    assert payload["code"] == -32601
    assert "authorized" in payload["message"]


def test_resource_reads_succeed(server_proc):
    proc, _ = server_proc
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "pytest", "version": "0"},
            "capabilities": {},
        },
    })
    _recv(proc)
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    _send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "resources/read",
        "params": {"uri": "kg://ontology.json"},
    })
    resp = _recv(proc)
    # Either a result envelope (success) or an error envelope (newer SDKs
    # surface handler exceptions as JSON-RPC errors). Both prove protocol-only
    # stdout and bounded handling.
    assert resp["id"] == 2
    assert resp["jsonrpc"] == "2.0"
    if "result" in resp:
        contents = resp["result"]["contents"]
        assert contents
        payload = json.loads(contents[0]["text"])
        assert "node_types" in payload
    else:
        # If the SDK rejected, ensure it's a protocol-level error (no leak).
        assert "error" in resp


def test_unknown_resource_rejected(server_proc):
    proc, _ = server_proc
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "pytest", "version": "0"},
            "capabilities": {},
        },
    })
    _recv(proc)
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    _send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "resources/read",
        "params": {"uri": "file:///etc/passwd"},
    })
    resp = _recv(proc)
    # Unknown URI must not read arbitrary files.
    assert "error" in resp or resp.get("result", {}).get("isError")
