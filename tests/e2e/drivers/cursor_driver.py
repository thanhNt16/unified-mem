"""Cursor driver for M5 T5 — single SessionDriver ABC (harness.py).

Two paths, in priority order (per preflight §5, §8):
  1. If a Cursor headless CLI entrypoint exists (``cursor --help`` lists an
     agent/MCP-driver flag), spawn it pointed at the installed kg MCP server.
  2. Otherwise, drive ``kg mcp serve`` stdio directly. This is the SAME MCP
     transport Cursor's UI would use, so Layer-1 portability is exercised
     identically either way.

Documented path: ``self.path_taken`` records which branch was used so the
E2E test can surface it in failure messages.

The driver subclasses the SINGLE ``SessionDriver`` ABC from harness.py —
there is no second concrete SessionDriver in this package. ``_spawn``
launches the kg MCP stdio server (Layer 1) or the Cursor binary (Layer 2);
``_send`` is a no-op for Layer 1 (deterministic tests drive ``mcp_client``
directly) and a one-shot ``--print`` invocation for Layer 2.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.e2e.harness import (
    McpStdioClient,
    SessionDriver,
    SkipLayer2,
    build_clean_env,
    mcp_stdio_session,
)


class CursorDriver(SessionDriver):
    """Cursor driver — single SessionDriver subclass.

    Layer 1 (deterministic): always drives ``kg mcp serve`` stdio via
    ``mcp_stdio_session``. ``_spawn`` returns the stdio Popen and stashes
    the McpStdioClient on ``self.mcp_client``; ``path_taken`` records the
    branch. This is the real portability surface — Cursor's UI talks the
    same MCP server.

    Layer 2 (live LLM): ``layer="live"`` + a Cursor binary + API key.
    ``_spawn`` only spawns Cursor if ``cursor --help`` lists a headless
    entrypoint; otherwise it raises ``SkipLayer2`` so the test skips
    cleanly. ``_send`` invokes ``cursor --print <msg>`` (the closest
    analog to Claude's print mode).
    """

    name = "cursor"
    # Layer-1 stdio is bounded by the McpStdioClient per-call timeout;
    # Layer-2 live Cursor can take longer.
    hard_timeout = 60.0

    def __init__(
        self,
        *,
        project_root: Path,
        home: Path,
        layer: str = "deterministic",
        prompt: str | None = None,
        kg_binary: str = "kg",
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            project_root=project_root,
            home_root=Path(home),
            timeout=timeout,
        )
        self.layer = layer
        self.prompt = prompt
        self.kg_binary = kg_binary
        # Populated by _spawn.
        self.path_taken: str = ""
        self.mcp_client: Any = None
        self.cursor_flag: str | None = None  # discovered via _cursor_find_agent_flag
        # CursorDriver builds its OWN env (it may need secrets for Layer 2);
        # we cache it for _send to reuse. SessionDriver.stop() clears _env.
        self._cursor_env: dict[str, str] | None = None

    # ---- path selection -------------------------------------------------

    @staticmethod
    def cursor_binary() -> str | None:
        """Return path to ``cursor`` binary or None."""
        return shutil.which("cursor")

    @staticmethod
    def _cursor_find_agent_flag(binary: str, *, timeout: float = 5.0) -> str | None:
        """Return the headless/agent/MCP-driver flag advertised by ``cursor --help``.

        Cursor's CLI is undocumented and changes often. We only attempt to
        drive it if ``--help`` lists a recognizable agent entrypoint. Any
        error (timeout, non-zero exit, parse failure) returns None — the
        caller raises SkipLayer2 so the test skips cleanly.

        Recognized flags (priority order): ``--mcp-driver``, ``--print``,
        ``--agent``, ``--headless``.
        """
        try:
            proc = subprocess.run(
                [binary, "--help"],
                capture_output=True, text=True, timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        text = (proc.stdout + proc.stderr).lower()
        for flag in ("--mcp-driver", "--print", "--agent", "--headless"):
            if flag in text:
                return flag
        return None

    # ---- SessionDriver hooks --------------------------------------------

    def _spawn(self, env: dict[str, str]) -> subprocess.Popen:
        """Launch the appropriate process for the selected layer."""
        # CursorDriver needs a different env than the ABC default for Layer 2
        # (CURSOR_API_KEY/ANTHROPIC_API_KEY). Rebuild here.
        self._cursor_env = env
        if self.layer == "live":
            return self._spawn_live()
        return self._spawn_stdio()

    def _send(self, message: str) -> str:
        """Layer 2: one-shot Cursor using the discovered flag. Layer 1: no-op."""
        if self.layer != "live":
            # Deterministic tests drive self.mcp_client directly; nothing
            # to send to a stdio JSON-RPC server via the ABC send() path.
            return ""
        if self._proc is None or self._proc.poll() is not None:
            raise SkipLayer2("cursor process not running for live layer")
        flag = self.cursor_flag
        if not flag:
            raise SkipLayer2("cursor headless flag was not discovered")
        env = self._cursor_env or build_clean_env(self.home_root, include_secrets=True)
        argv = [self.cursor_binary() or "cursor", flag, message]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.project_root),
                env=env,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise SkipLayer2(f"cursor {flag} failed: {exc}") from exc
        return proc.stdout

    # ---- spawn branches -------------------------------------------------

    def _spawn_stdio(self) -> subprocess.Popen:
        """Layer 1: drive ``kg mcp serve`` stdio. Always available."""
        # Layer-1 deterministic: NO secrets propagation.
        env = build_clean_env(self.home_root)
        kg_argv = self._resolve_kg_argv()
        proc, client = mcp_stdio_session(
            kg_argv,
            cwd=self.project_root,
            env=env,
            timeout=self.hard_timeout,
        )
        self.mcp_client = client
        self.path_taken = "stdio:kg mcp serve"
        return proc

    def _spawn_live(self) -> subprocess.Popen:
        """Layer 2: spawn Cursor binary using the headless flag it advertises."""
        binary = self.cursor_binary()
        if not binary:
            raise SkipLayer2("no `cursor` binary on PATH")
        if not os.environ.get("CURSOR_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
            raise SkipLayer2("no API key env (CURSOR_API_KEY/ANTHROPIC_API_KEY)")
        if not self.prompt:
            raise SkipLayer2("live layer requires a prompt")
        flag = self._cursor_find_agent_flag(binary)
        if not flag:
            raise SkipLayer2("`cursor --help` advertises no headless/agent flag")
        self.cursor_flag = flag
        # Rebuild env with the cursor key propagated. We intentionally do NOT
        # inherit the project's MCP config — we exercise the same installed
        # kg MCP server that the deterministic layer drives.
        env = build_clean_env(self.home_root, include_secrets=True)
        argv = [binary, flag, self.prompt]
        self.path_taken = f"cursor:{binary} {flag}"
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self.project_root),
            env=env,
        )
        self._cursor_env = env
        return proc

    def _resolve_kg_argv(self) -> list[str]:
        """Prefer installed ``kg`` binary; fall back to ``python -m kg``."""
        if self.kg_binary and shutil.which(self.kg_binary):
            return [self.kg_binary, "mcp", "serve", "--project-root", str(self.project_root)]
        return [sys.executable, "-m", "kg", "mcp", "serve", "--project-root", str(self.project_root)]
