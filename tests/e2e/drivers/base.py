"""SessionDriver base: shared process hygiene for live-session E2E drivers.

Every driver inherits:
  * ``build_clean_env`` — only PATH/HOME (+ caller-supplied allowlist).
    Never logs env (could leak API keys).
  * Hard ``timeout`` + ``proc.kill()`` — no orphans.
  * Layer-1 deterministic path drives ``kg mcp serve`` stdio (the real
    portability surface — every harness talks the same MCP server).

ponytail: no streaming/async. Add: when a second transport (websocket)
lands, factor ``McpStdioSession`` out. Until then one function is enough.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


# env vars we always copy through (none carry secrets). HOME is set per-call.
# API keys live in _ENV_SECRETS and only propagate when include_secrets=True.
_ENV_BASE = (
    "PATH",
    "LANG", "LC_ALL", "LC_CTYPE",  # locale — needed for cursor/claude UIs
    "TZ",  # deterministic timestamps in logs
)
_ENV_SECRETS = (
    "ANTHROPIC_API_KEY",  # live-session layer only; never echoed
    "CURSOR_API_KEY",
)


def build_clean_env(
    home: Path | str,
    *,
    extra: dict[str, str] | None = None,
    include_secrets: bool = False,
) -> dict[str, str]:
    """Return a minimal env for subprocess drivers.

    Contains only PATH/HOME/locale + caller-supplied extra keys. Drops
    everything else so harnesses cannot inherit shells, tokens, or
    per-user config that would make tests non-portable.

    ``include_secrets=False`` (default) drops API keys entirely — Layer 1
    deterministic tests must NEVER propagate secrets. Layer 2 live-LLM
    callers set ``include_secrets=True`` and pass any explicit extras.

    Never log the result.
    """
    base_allow = _ENV_BASE
    if include_secrets:
        base_allow = base_allow + _ENV_SECRETS
    env: dict[str, str] = {}
    for key in base_allow:
        if key in os.environ:
            env[key] = os.environ[key]
    env["HOME"] = str(home)
    if extra:
        env.update(extra)
    return env


class SessionDriver:
    """Base driver: spawns a process, enforces timeout, guarantees kill.

    Subclasses set ``self.argv`` (list[str]) and ``self.cwd`` (Path|None)
    before calling ``super().start()``. They never override ``stop`` —
    the hygiene guarantees (kill + wait) are universal.
    """

    name: str = "base"
    # Default 30s — generous for cold starts, bounded for CI. Live-LLM
    # subclasses override to 60-120s.
    hard_timeout: float = 30.0

    def __init__(self) -> None:
        # subclasses populate these
        self.argv: list[str] = []
        self.cwd: Path | None = None
        self.env: dict[str, str] = {}
        self._proc: subprocess.Popen | None = None

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> subprocess.Popen:
        if self._proc is not None:
            return self._proc
        if not self.argv:
            raise RuntimeError("driver argv not set")
        self._proc = subprocess.Popen(
            self.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
        )
        return self._proc

    def stop(self, *, timeout: float = 5.0) -> tuple[str, str]:
        """Kill the process and wait. Returns (drained stdout, stderr).

        Idempotent. Order matters: kill the child first, then drain
        pipes. Reading while the child is alive deadlocks if the child
        is blocked on its own stdout write.
        """
        if self._proc is None:
            return "", ""
        proc = self._proc
        # Hard kill — SIGKILL is reliable and never blocked. For test
        # cleanup we don't need graceful shutdown.
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass  # leak rather than hang

        out = self._drain(proc.stdout)
        err = self._drain(proc.stderr)
        self._proc = None
        return out, err

    @staticmethod
    def _drain(stream) -> str:
        """Best-effort drain after the child is dead. Returns '' on error."""
        if stream is None:
            return ""
        try:
            return stream.read() or ""
        except (OSError, ValueError):
            return ""

    # ---- context manager ------------------------------------------------

    def __enter__(self) -> "SessionDriver":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def which(binary: str) -> str | None:
        """PATH lookup that subclasses can mock in tests."""
        return shutil.which(binary)


def mcp_stdio_session(
    kg_argv: Iterable[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 30.0,
) -> tuple[subprocess.Popen, "McpStdioClient"]:
    """Start ``kg mcp serve`` over stdio and return (proc, client).

    This is the Layer-1 path: deterministic, CI-safe, no LLM. Used by
    tests that need to assert structural MCP responses from a freshly
    installed kg binary (the same surface Cursor/Claude would talk to).
    """
    proc = subprocess.Popen(
        list(kg_argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(cwd),
        env=env,
    )
    client = McpStdioClient(proc, timeout=timeout)
    return proc, client


class McpStdioClient:
    """Minimal JSON-RPC over stdio client for one-shot tool calls.

    Each method sends one request and reads one response. No streaming,
    no async — the goal is structural assertions, not load testing.
    """

    def __init__(self, proc: subprocess.Popen, *, timeout: float = 30.0) -> None:
        self.proc = proc
        self.timeout = timeout
        self._next_id = 1

    def send(self, payload: dict[str, Any]) -> None:
        assert self.proc.stdin is not None, "process has no stdin"
        line = json.dumps(payload) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def recv(self, *, timeout: float | None = None) -> dict[str, Any]:
        assert self.proc.stdout is not None, "process has no stdout"
        wait = timeout if timeout is not None else self.timeout
        rlist, _, _ = select.select([self.proc.stdout], [], [], wait)
        assert rlist, "no stdout within timeout"
        line = self.proc.stdout.readline()
        assert line, "stdout closed"
        return json.loads(line)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)
        return self.recv()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)

    def initialize(self) -> dict[str, Any]:
        return self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "kg-e2e-driver", "version": "0"},
            "capabilities": {},
        })

    def list_tools(self) -> dict[str, Any]:
        return self.request("tools/list")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
