"""SessionDriver ABC + MCP stdio helpers for E2E distribution tests.

Determinism contract:
  - This library spawns NO LLM and reads no model output. The driver
    implementation (Claude, Cursor, etc.) is responsible for any LLM I/O.
  - The environment handed to the spawned process contains ONLY PATH, HOME,
    and (if provided) the API key. No other env is passed — and the env is
    never logged.
  - stop() force-kills the process tree on timeout.

Context manager protocol:
    with SomeDriver(project_root, home_root=tmp, api_key=k) as drv:
        drv.send("hello")
        drv.wait_for(lambda: graph_has_nodes(drv.project_root, 1))

MCP stdio helpers:
    proc, client = mcp_stdio_session(argv, cwd=..., env=...)
    client.initialize(); client.call_tool("search_memory", {...})

ponytail: no streaming/async in McpStdioClient. Add: when a second transport
(websocket) lands, factor McpStdioSession out. Until then one function is enough.
"""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Iterable


class CleanEnvError(RuntimeError):
    """Raised when the caller asks to log the env or passes a forbidden var."""


# Keys that must NEVER leak into the spawned process: tokens, prior agent
# state, shell secrets. Whitelist enforcement means we only ever pass
# PATH + HOME + (optional) API key — anything else is a bug.
_FORBIDDEN_ENV_KEYS = frozenset({
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "API_KEY",
    "CLAUDE_CODE_ENTRYPOINT",
    "MASTODON_TOKEN", "GITHUB_TOKEN",
    # agent harnesses read these and behave differently — scrub.
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "MCP_TIMEOUT",
})


def build_clean_env(
    home_root: Path,
    *,
    api_key: str | None = None,
    extra_path: list[Path] | None = None,
    include_secrets: bool = False,
) -> dict[str, str]:
    """Build a minimal env: only PATH, HOME, and (if given) ANTHROPIC_API_KEY.

    No other env is passed. The returned dict is safe to log minus the api
    key — callers MUST NOT log it. We deliberately do not inherit os.environ
    so a dirty developer shell cannot influence E2E results.

    Args:
        home_root: directory to use as HOME for the spawned process.
        api_key: optional Anthropic API key. Stored under ANTHROPIC_API_KEY.
        extra_path: additional PATH entries prepended (e.g. uv tool bin dir).
        include_secrets: if True, also propagate ANTHROPIC_API_KEY and
            CURSOR_API_KEY from os.environ. Used ONLY by Layer-2 live-LLM
            callers. Layer-1 deterministic tests must NEVER set this.

    Raises:
        CleanEnvError: if api_key is empty-string (None is allowed = skip).
    """
    if api_key is not None and not api_key.strip():
        raise CleanEnvError("api_key must be non-empty or None")

    home_root = Path(home_root)
    home_root.mkdir(parents=True, exist_ok=True)

    # Build PATH: caller-supplied entries first, then a sane minimal system
    # PATH, then the inherited PATH last (so a developer's overrides still
    # resolve binaries like `claude`, but our explicit entries win).
    base_path = os.defpath if os.defpath else "/usr/local/bin:/usr/bin:/bin"
    inherited = os.environ.get("PATH", "")
    parts: list[str] = []
    if extra_path:
        parts.extend(str(p) for p in extra_path)
    parts.append(base_path)
    if inherited:
        parts.append(inherited)
    path = ":".join(dict.fromkeys(parts))  # dedup, preserve order

    env: dict[str, str] = {
        "PATH": path,
        "HOME": str(home_root),
    }
    if api_key is not None:
        env["ANTHROPIC_API_KEY"] = api_key
    if include_secrets:
        # Layer-2 only: propagate explicit live-LLM secrets from os.environ.
        for k in ("ANTHROPIC_API_KEY", "CURSOR_API_KEY"):
            v = os.environ.get(k)
            if v:
                env[k] = v
    return env


class SessionDriver(ABC):
    """ABC for a harness driver: spawn binary, send message, wait for state.

    Concrete drivers (ClaudeDriver, CursorDriver, ...) implement `_spawn`
    to launch the actual process and `_send` to deliver a message and return
    the captured output. Everything else — env scrubbing, timeout, kill,
    context-manager lifecycle — lives here.

    Lifecycle:
        drv = SomeDriver(project_root=..., home_root=..., api_key=...)
        drv.start()        # spawns the subprocess via _spawn
        out = drv.send("hello")
        drv.wait_for(lambda: ..., timeout=60)
        drv.stop()         # terminate + reap, or SIGKILL on timeout

    The driver NEVER reads the inherited os.environ; it builds a clean env
    from build_clean_env(). The driver NEVER logs the env dict.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        home_root: Path,
        api_key: str | None = None,
        timeout: float = 120.0,
        extra_path: list[Path] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.home_root = Path(home_root)
        self.api_key = api_key
        self.timeout = float(timeout)
        self.extra_path = extra_path
        self._proc: subprocess.Popen | None = None
        self._started_at: float | None = None
        # Built once at start(); never logged.
        self._env: dict[str, str] | None = None

    # ---- public API ---------------------------------------------------------

    def start(self) -> "SessionDriver":
        if self._proc is not None:
            raise RuntimeError("SessionDriver already started")
        self._env = build_clean_env(
            self.home_root, api_key=self.api_key, extra_path=self.extra_path,
        )
        self._proc = self._spawn(self._env)
        self._started_at = time.monotonic()
        return self

    def send(self, message: str) -> str:
        """Send a message; return captured stdout (or combined output).

        Implementations decide what 'output' means (stream-json, plain text).
        Must not exceed self.timeout wall-clock since start().
        """
        self._require_running()
        self._check_deadline()
        return self._send(message)

    def wait_for(
        self,
        predicate: Callable[[], bool],
        timeout: float | None = None,
        *,
        interval: float = 0.5,
    ) -> bool:
        """Poll predicate until True or timeout. Returns True on success.

        timeout defaults to self.timeout. Raises TimeoutError on expiry.
        """
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        # One last shot — predicate may have flipped during the final sleep.
        if predicate():
            return True
        raise TimeoutError(
            f"wait_for gave up after {timeout if timeout is not None else self.timeout}s"
        )

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _kill_tree(proc.pid)
                    proc.wait(timeout=5)
        finally:
            self._proc = None
            self._env = None
            self._started_at = None

    # context manager
    def __enter__(self) -> "SessionDriver":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---- hooks for concrete drivers ----------------------------------------

    @abstractmethod
    def _spawn(self, env: dict[str, str]) -> subprocess.Popen:
        """Launch the harness binary. Must use the provided env verbatim."""
        raise NotImplementedError

    @abstractmethod
    def _send(self, message: str) -> str:
        """Deliver one message to the running process; return its output."""
        raise NotImplementedError

    # ---- internals ----------------------------------------------------------

    def _require_running(self) -> subprocess.Popen:
        if self._proc is None:
            raise RuntimeError("SessionDriver not started; call start() first")
        return self._proc

    def _check_deadline(self) -> None:
        if self._started_at is None:
            return
        if time.monotonic() - self._started_at > self.timeout:
            raise TimeoutError(
                f"SessionDriver deadline ({self.timeout}s) exceeded before send()"
            )


def _kill_tree(pid: int) -> None:
    """SIGKILL a process and all its children (best-effort, POSIX)."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        # pgrep for children; ignore failures (non-macOS, no pgrep, etc.).
        out = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True, text=True, check=False,
        )
        for line in out.stdout.split():
            try:
                child = int(line)
            except ValueError:
                continue
            _kill_tree(child)
    except FileNotFoundError:
        pass


# ── MCP stdio helpers ──────────────────────────────────────────────────────
# Layer-1 portability surface: every harness (Claude, Cursor, ...) talks to
# the same kg MCP server over stdio. These helpers spawn that server and
# frame one-shot JSON-RPC so tests can assert structural responses without
# any real LLM.

def mcp_stdio_session(
    kg_argv: Iterable[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 30.0,
) -> tuple[subprocess.Popen, "McpStdioClient"]:
    """Start ``kg mcp serve`` over stdio and return (proc, client).

    Used by drivers/tests that need to assert structural MCP responses from a
    freshly installed kg binary (the same surface Cursor/Claude would talk to).
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


class SkipLayer2(RuntimeError):
    """Raised when a Layer-2 (live-LLM) driver cannot run.

    The test converts this to ``pytest.skip`` so CI stays green when the
    required binary / API key is absent.
    """

