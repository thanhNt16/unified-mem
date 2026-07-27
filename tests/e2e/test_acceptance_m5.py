"""M5 acceptance — distribution & portability headline.

Single deterministic test exercises the full distribution flow once:
    bootstrap (kg installed globally)
    -> seed raw + save golden
    -> kg search (keyword)
    -> kg mcp serve (stdio, two harnesses via global binary)
    -> uninstall reverses everything

Portability promise: same ``kg.db`` is queried through MCP stdio transport
twice (simulating two distinct harnesses connecting to one knowledge base);
the structural responses (node ids, edge counts, expansion) must be equal.
LLM prose and vector rankings are out of scope (see preflight correction #4).

Skips cleanly when the global ``kg`` is not on PATH (CI bootstrap tier runs
this; dev worktrees without ``make install`` do not).

Complements T6's focused portability test (install -> use -> uninstall
lifecycle) by exercising the full bootstrap -> search -> MCP -> uninstall
loop end-to-end.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

runner_skip = pytest.mark.skipif(
    shutil.which("kg") is None,
    reason="global `kg` not on PATH — run `make install` or this CI tier",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(args: list[str], *, cwd: Path, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"command failed: {args}\nexit={proc.returncode}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


def _rpc(proc: subprocess.Popen, message: dict) -> dict:
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise AssertionError(
            f"no JSON-RPC response\nmessage={message}\n"
            f"returncode={proc.poll()}\nstderr={stderr}"
        )
    return json.loads(line)


def _start_mcp_stdio(kg_bin: str, project: Path, env: dict) -> subprocess.Popen:
    """Spawn ``kg mcp serve`` via the globally installed binary."""
    return subprocess.Popen(
        [kg_bin, "mcp", "serve", "--project-root", str(project)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env,
    )


def _mcp_search(proc: subprocess.Popen, query: str, *, rpc_id: int) -> dict:
    """Initialize (idempotent) then call search_memory; return raw result envelope."""
    _rpc(proc, {
        "jsonrpc": "2.0", "id": rpc_id, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "m5-acceptance", "version": "1"},
            "capabilities": {},
        },
    })
    assert proc.stdin
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()
    return _rpc(proc, {
        "jsonrpc": "2.0", "id": rpc_id + 1, "method": "tools/call",
        "params": {
            "name": "search_memory",
            "arguments": {"query": query, "mode": "keyword", "k": 5},
        },
    })


# ---------------------------------------------------------------------------
# Seed corpus (deterministic; FakeEmbedder is irrelevant for keyword search)
# ---------------------------------------------------------------------------

_SEED_NODES = [
    {"type": "person", "name": "Ada Lovelace", "summary": "First programmer."},
    {"type": "person", "name": "Charles Babbage", "summary": "Analytical engine designer."},
    {"type": "organization", "name": "Royal Society", "summary": "Scientific academy."},
    {"type": "object", "subtype": "software", "name": "Analytical Engine",
     "summary": "Mechanical general-purpose computer."},
]
_SEED_EDGES = [
    # semantic_type must be in ALLOWED_SEMANTIC_EDGE_TYPES (kg.ontology).
    {"source_name": "Ada Lovelace", "semantic_type": "knows",
     "target_name": "Charles Babbage"},
    {"source_name": "Charles Babbage", "semantic_type": "owns",
     "target_name": "Analytical Engine"},
    {"source_name": "Ada Lovelace", "semantic_type": "member_of",
     "target_name": "Royal Society"},
]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@runner_skip
def test_m5_distribution_portability(tmp_path):
    """Single test runs full bootstrap -> save -> search -> MCP -> reversibility.

    Portability promise under test: the same ``kg.db`` queried twice through
    MCP stdio (as if two different harnesses connected to it) returns
    structurally-equal tool responses.
    """
    kg_bin = shutil.which("kg")
    assert kg_bin, "kg not on PATH"

    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()

    # Clean env: only PATH, HOME, API key (none here). Never leak extra.
    base_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
    }

    # 1. Bootstrap: project init via global kg.
    _run([kg_bin, "init", "--user-id", "m5", "--scope", "acceptance"],
         cwd=project, env=base_env)

    # 2. Save golden corpus.
    nodes_file = project / "nodes.json"
    edges_file = project / "edges.json"
    nodes_file.write_text(json.dumps(_SEED_NODES), encoding="utf-8")
    edges_file.write_text(json.dumps(_SEED_EDGES), encoding="utf-8")
    _run([kg_bin, "save",
          "--nodes", str(nodes_file),
          "--edges", str(edges_file),
          "--source", "raw/seed.md#chunk-0"],
         cwd=project, env=base_env)

    # 3. CLI keyword search returns the seed. ``query`` is positional.
    cli_search = _run(
        [kg_bin, "search", "Lovelace", "--mode", "keyword", "-k", "5"],
        cwd=project, env=base_env,
    )
    assert "Ada Lovelace" in cli_search.stdout or "Lovelace" in cli_search.stdout, (
        f"CLI search missed seed:\n{cli_search.stdout}\n{cli_search.stderr}"
    )

    # 4. Portability: same kg.db queried twice via MCP stdio.
    harness_a = _start_mcp_stdio(kg_bin, project, base_env)
    harness_b = _start_mcp_stdio(kg_bin, project, base_env)
    try:
        result_a = _mcp_search(harness_a, "Lovelace", rpc_id=10)
        result_b = _mcp_search(harness_b, "Lovelace", rpc_id=20)
    finally:
        for p in (harness_a, harness_b):
            p.kill()
            p.wait(timeout=5)

    # Both must be successful tool calls with structured content.
    for tag, res in (("a", result_a), ("b", result_b)):
        assert res.get("jsonrpc") == "2.0", f"bad frame {tag}: {res}"
        assert "result" in res, f"missing result {tag}: {res}"
        assert not res["result"].get("isError"), f"tool error {tag}: {res}"

    # Structural equality: same node ids, same edge count, same shape.
    content_a = json.dumps(result_a["result"]["content"], sort_keys=True)
    content_b = json.dumps(result_b["result"]["content"], sort_keys=True)
    assert content_a == content_b, (
        "MCP stdio responses differ across two transport instances on the "
        f"same kg.db:\nA={content_a}\nB={content_b}"
    )

    # 5. Reversibility: uninstall removes the installer manifest.
    #    Use --apply path first so manifest exists, then --uninstall.
    skills_src = Path(__file__).resolve().parents[2] / "skills"
    if skills_src.is_dir():
        _run([kg_bin, "install", "claude", "--apply",
              "--home-root", str(home), "--skills-src", str(skills_src)],
             cwd=project, env=base_env)
        _run([kg_bin, "install", "claude", "--uninstall",
              "--home-root", str(home)],
             cwd=project, env=base_env)
        manifest = project / ".kg-install-manifest.json"
        assert not manifest.exists(), f"uninstall left manifest: {manifest}"

    # kg.db persisted across transports (the portability substrate).
    assert (project / ".kg" / "kg.db").exists(), "kg.db missing after MCP roundtrip"
