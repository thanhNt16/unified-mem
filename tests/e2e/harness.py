"""SessionDriver ABC and clean-env builder for E2E distribution tests.

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
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable


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
) -> dict[str, str]:
    """Build a minimal env: only PATH, HOME, and (if given) ANTHROPIC_API_KEY.

    No other env is passed. The returned dict is safe to log minus the api
    key — callers MUST NOT log it. We deliberately do not inherit os.environ
    so a dirty developer shell cannot influence E2E results.

    Args:
        home_root: directory to use as HOME for the spawned process.
        api_key: optional Anthropic API key. Stored under ANTHROPIC_API_KEY.
        extra_path: additional PATH entries prepended (e.g. uv tool bin dir).

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
