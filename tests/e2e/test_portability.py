"""M5 T6: portability E2E — same .kg/ answers identically across two MCP stdio transports.

Layer 1 (deterministic, no LLM/network): seed ONE .kg/ with FakeEmbedder,
launch TWO independent MCP server subprocesses (simulating Claude-style and
Cursor-style transports), run identical kg tool calls via stdio JSON-RPC,
assert structured JSON responses are structurally equal.

The "two transports" are: (a) stdio server with default clientInfo name=claude,
(b) stdio server with clientInfo name=cursor. Both hit the same kg.db.
This proves the kg MCP layer is harness-agnostic.

ponytail: Layer 2 (real harnesses + API keys) deferred — gated on both binaries
present. Would assert graph grew in both after live session."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.e2e.assertions import kg_tool_json_equal


# -- Deterministic seed data --------------------------------------------------

SEED_NODES = [
    {"type": "person", "name": "Ada Lovelace", "summary": "first programmer"},
    {"type": "person", "name": "Charles Babbage", "summary": "inventor of analytical engine"},
    {"type": "organization", "name": "Royal Society", "summary": "scientific academy"},
    {"type": "event", "name": "Demonstration of Analytical Engine", "summary": "1843 event"},
]
SEED_EDGES = [
    {"source_name": "Ada Lovelace", "target_name": "Charles Babbage", "semantic_type": "knows", "confidence": 1.0},
    {"source_name": "Ada Lovelace", "target_name": "Royal Society", "semantic_type": "member_of", "confidence": 1.0},
    {"source_name": "Charles Babbage", "target_name": "Demonstration of Analytical Engine", "semantic_type": "related_to", "confidence": 0.9},
]
SOURCE = "raw/portability-test.md#chunk-0"

SEED_SCRIPT = textwrap.dedent("""
    import sys, json
    from pathlib import Path
    from kg.cli.init import init_project
    from kg.embed import FakeEmbedder
    from kg.mcp.handlers import save_pole

    root = Path(sys.argv[1])
    init_project(root, user_id="u", scope="portability-e2e")

    nodes = json.loads(sys.argv[2])
    edges = json.loads(sys.argv[3])
    source = sys.argv[4]

    save_pole(root, nodes=nodes, edges=edges, source=source,
              authorized=True, embedder=FakeEmbedder())
    print("SEEDED")
""")


# -- Stdio MCP transport helpers ----------------------------------------------

# ponytail: reuses _send/_recv pattern from tests/mcp/test_server.py.
# If MCP SDK transport wrapper lands, replace with shared util.


def _send(proc: subprocess.Popen, payload: dict) -> None:
    line = json.dumps(payload) + "\n"
    assert proc.stdin is not None
    proc.stdin.write(line)
    proc.stdin.flush()


def _recv(proc: subprocess.Popen, timeout: float = 15.0) -> dict:
    import select
    assert proc.stdout is not None
    rlist, _, _ = select.select([proc.stdout], [], [], timeout)
    assert rlist, "no stdout within timeout"
    line = proc.stdout.readline()
    assert line, "stdout closed"
    return json.loads(line)


def _init_transport(proc: subprocess.Popen, client_name: str) -> None:
    """Complete MCP initialize handshake with a given client identity."""
    _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": client_name, "version": "0"},
            "capabilities": {},
        },
    })
    resp = _recv(proc)
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "kg"
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})


def _call_tool(proc: subprocess.Popen, req_id: int, name: str, arguments: dict) -> dict:
    """Send a tools/call and return structuredContent."""
    _send(proc, {
        "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    resp = _recv(proc)
    assert resp["id"] == req_id
    assert resp["result"]["isError"] is False, f"tool error: {resp['result']}"
    return resp["result"]["structuredContent"]


def _launch_server(root: Path) -> subprocess.Popen:
    """Launch kg MCP stdio server subprocess against root."""
    server_py = Path(__file__).resolve().parent.parent / "mcp" / "stdio_server.py"
    proc = subprocess.Popen(
        [sys.executable, str(server_py), str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    return proc


def _kill(proc: subprocess.Popen) -> None:
    proc.kill()
    proc.wait(timeout=5)


# -- Structural equality for kg tool JSON ------------------------------------
# ``kg_tool_json_equal`` comes from assertions.py. Its shared unstable-field
# policy ignores score/distance/embedding/timestamps consistently across E2E.


def _normalize_search(content: dict) -> dict:
    """Normalize search results for comparison: sort by node_id, drop score."""
    results = sorted(content["results"], key=lambda r: r["node_id"])
    for r in results:
        r.pop("score", None)
    return {"results": results}


def _normalize_expand(content: dict) -> dict:
    """Normalize expand results: sort nodes/edges by id, drop embeddings."""
    nodes = sorted(content["nodes"], key=lambda n: n["id"])
    for n in nodes:
        n.pop("embedding", None)
    edges = sorted(content["edges"], key=lambda e: e["id"])
    return {
        "nodes": nodes,
        "edges": edges,
        "truncated_count": content["truncated_count"],
    }


# -- Fixtures ------------------------------------------------------------------


@pytest.fixture(scope="module")
def shared_kg(tmp_path_factory):
    """Seed ONE .kg/ store used by both transports."""
    root = tmp_path_factory.mktemp("portability") / "proj"
    root.mkdir()

    # Subprocess isolates the seeded kg.db and avoids in-process state sharing.
    result = subprocess.run(
        [sys.executable, "-c", SEED_SCRIPT, str(root),
         json.dumps(SEED_NODES), json.dumps(SEED_EDGES), SOURCE],
        capture_output=True, text=True, check=True,
    )
    assert "SEEDED" in result.stdout
    yield root


@pytest.fixture
def claude_transport(shared_kg: Path):
    """(a) Claude-style MCP stdio transport."""
    proc = _launch_server(shared_kg)
    _init_transport(proc, client_name="claude")
    try:
        yield proc
    finally:
        _kill(proc)


@pytest.fixture
def cursor_transport(shared_kg: Path):
    """(b) Cursor-style MCP stdio transport."""
    proc = _launch_server(shared_kg)
    _init_transport(proc, client_name="cursor")
    try:
        yield proc
    finally:
        _kill(proc)


# -- Tests ---------------------------------------------------------------------


def test_search_memory_structurally_equal(claude_transport, cursor_transport):
    """search_memory --mode keyword returns identical node sets from both transports."""
    claude_result = _call_tool(
        claude_transport, req_id=10, name="search_memory",
        arguments={"query": "Lovelace", "mode": "keyword", "k": 10},
    )
    cursor_result = _call_tool(
        cursor_transport, req_id=10, name="search_memory",
        arguments={"query": "Lovelace", "mode": "keyword", "k": 10},
    )

    claude_norm = _normalize_search(claude_result)
    cursor_norm = _normalize_search(cursor_result)

    claude_ids = {r["node_id"] for r in claude_norm["results"]}
    cursor_ids = {r["node_id"] for r in cursor_norm["results"]}

    assert claude_ids == cursor_ids, f"node-id mismatch: claude={claude_ids} cursor={cursor_ids}"
    assert len(claude_norm["results"]) >= 1, "expected at least one Lovelace result"
    # The Ada Lovelace node must be present.
    assert any("lovelace" in r["name"].lower() for r in claude_norm["results"])
    # Structural equality.
    assert kg_tool_json_equal(claude_norm, cursor_norm)


def test_expand_memory_structurally_equal(claude_transport, cursor_transport):
    """expand_memory returns identical subgraph from both transports."""
    # First find a node id via search.
    search_result = _call_tool(
        claude_transport, req_id=20, name="search_memory",
        arguments={"query": "Ada Lovelace", "mode": "keyword", "k": 1},
    )
    seed_id = search_result["results"][0]["node_id"]

    claude_result = _call_tool(
        claude_transport, req_id=21, name="expand_memory",
        arguments={"seed_ids": [seed_id], "hops": 2, "direction": "both"},
    )
    cursor_result = _call_tool(
        cursor_transport, req_id=21, name="expand_memory",
        arguments={"seed_ids": [seed_id], "hops": 2, "direction": "both"},
    )

    claude_norm = _normalize_expand(claude_result)
    cursor_norm = _normalize_expand(cursor_result)

    # Node-id sets must match.
    claude_node_ids = {n["id"] for n in claude_norm["nodes"]}
    cursor_node_ids = {n["id"] for n in cursor_norm["nodes"]}
    assert claude_node_ids == cursor_node_ids, (
        f"expand node-id mismatch: claude={claude_node_ids} cursor={cursor_node_ids}"
    )

    # Edge counts must match.
    assert len(claude_norm["edges"]) == len(cursor_norm["edges"]), (
        f"edge count mismatch: claude={len(claude_norm['edges'])} "
        f"cursor={len(cursor_norm['edges'])}"
    )

    # Full structural equality.
    assert kg_tool_json_equal(claude_norm, cursor_norm)


def test_search_query_different_results(claude_transport):
    """Sanity: two different queries return different node sets."""
    r1 = _normalize_search(_call_tool(
        claude_transport, req_id=30, name="search_memory",
        arguments={"query": "Lovelace", "mode": "keyword", "k": 10},
    ))
    r2 = _normalize_search(_call_tool(
        claude_transport, req_id=31, name="search_memory",
        arguments={"query": "Babbage", "mode": "keyword", "k": 10},
    ))
    ids1 = {r["node_id"] for r in r1["results"]}
    ids2 = {r["node_id"] for r in r2["results"]}
    assert ids1 != ids2, "different queries should yield different results"


# -- Layer 2 (optional, gated on real harnesses) --------------------------------

BOTH_HARNESSES = shutil.which("claude") and shutil.which("cursor")


@pytest.mark.skipif(not BOTH_HARNESSES, reason="needs both claude + cursor binaries")
def test_layer2_graph_grew_in_both(tmp_path):
    """Layer 2: if both real harnesses present, graph grew after live session.

    ponytail: not yet implemented — needs real harness session orchestration.
    This is the placeholder that CI will skip until live harness drivers land.
    """
    pytest.skip("Layer 2 live harness portability — deferred to live driver tasks")
