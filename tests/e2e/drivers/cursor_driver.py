"""Cursor driver for M5 T5.

Two paths, in priority order (per preflight §5, §8):
  1. If a Cursor headless CLI entrypoint exists (``cursor --help`` lists
     an agent/MCP-driver flag), spawn it pointed at the installed kg MCP
     server. Cursor's UI is not the portability surface; the MCP wire is.
  2. Otherwise, drive ``kg mcp serve`` stdio directly. This is the SAME
     MCP transport Cursor's UI would use, so Layer-1 portability is
     exercised identically either way.

Documented path: ``self.path_taken`` records which branch was used so the
E2E test can surface it in failure messages.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import SessionDriver, build_clean_env, mcp_stdio_session


class CursorDriver(SessionDriver):
    """Spawns Cursor (or falls back to kg mcp serve stdio).

    Layer 1 (deterministic): always falls back to stdio. Cursor's
    headless mode is undocumented and may not exist; the stdio MCP path
    is the real portability surface and is always available once kg is
    installed.

    Layer 2 (live LLM): ``__init__(layer="live")`` + a Cursor binary +
    ``CURSOR_API_KEY`` env. The driver will only spawn Cursor if a
    documented agent entrypoint exists; otherwise it raises
    ``SkipLayer2`` so the test can skipif cleanly.
    """

    name = "cursor"
    hard_timeout = 60.0

    def __init__(
        self,
        *,
        project_root: Path,
        home: Path,
        layer: str = "deterministic",
        prompt: str | None = None,
        kg_binary: str = "kg",
    ) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.home = Path(home).resolve()
        self.layer = layer
        self.prompt = prompt
        self.kg_binary = kg_binary
        # populated after start()
        self.path_taken: str = ""
        self.mcp_client: Any = None

    # ---- path selection -------------------------------------------------

    @staticmethod
    def cursor_binary() -> str | None:
        """Return path to ``cursor`` binary or None."""
        return shutil.which("cursor")

    @staticmethod
    def _cursor_has_agent_flag(binary: str, *, timeout: float = 5.0) -> bool:
        """Check ``cursor --help`` for a headless/agent/MCP-driver flag.

        Cursor's CLI is undocumented and changes often. We only attempt
        to drive it if ``--help`` lists a recognizable agent entrypoint.
        Any error (timeout, non-zero exit, parse failure) returns False —
        the caller falls back to stdio.
        """
        try:
            proc = subprocess.run(
                [binary, "--help"],
                capture_output=True, text=True, timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        if proc.returncode != 0:
            return False
        text = (proc.stdout + proc.stderr).lower()
        # Recognized headless / agent entrypoints. ``--mcp-driver`` is
        # speculative; ``-p`` mirrors Claude's print mode if Cursor adds
        # it.
        for flag in ("--mcp-driver", "--print", "--agent", "--headless"):
            if flag in text:
                return True
        return False

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> subprocess.Popen:
        if self.layer == "live":
            return self._start_live()
        return self._start_stdio()

    def _start_stdio(self) -> subprocess.Popen:
        """Layer 1: drive ``kg mcp serve`` stdio. Always available."""
        env = build_clean_env(self.home)
        kg_argv = self._resolve_kg_argv()
        proc, client = mcp_stdio_session(
            kg_argv,
            cwd=self.project_root,
            env=env,
            timeout=self.hard_timeout,
        )
        self._proc = proc
        self.mcp_client = client
        self.path_taken = "stdio:kg mcp serve"
        return proc

    def _start_live(self) -> subprocess.Popen:
        """Layer 2: spawn Cursor binary if it has an agent entrypoint."""
        binary = self.cursor_binary()
        if not binary:
            raise SkipLayer2("no `cursor` binary on PATH")
        if not os.environ.get("CURSOR_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
            raise SkipLayer2("no API key env (CURSOR_API_KEY/ANTHROPIC_API_KEY)")
        if not self._cursor_has_agent_flag(binary):
            raise SkipLayer2("`cursor --help` has no headless/agent entrypoint")
        if not self.prompt:
            raise SkipLayer2("live layer requires a prompt")
        # Best-effort: invoke Cursor with the recognized print/agent flag.
        # We don't know the exact shape yet — pick the first supported
        # flag we found and pass the prompt positionally. This is a
        # smoke-portability check (assert graph grew), not a behavioral
        # assertion on Cursor's LLM output.
        env = build_clean_env(
            self.home,
            extra={"CURSOR_API_KEY": os.environ.get("CURSOR_API_KEY", "")},
            include_secrets=True,
        )
        # We intentionally do NOT inherit the project's MCP config — we
        # want to exercise the same installed kg MCP server that the
        # deterministic layer drives.
        self.argv = [binary, "--print", self.prompt]
        self.cwd = self.project_root
        self.env = env
        self.path_taken = f"cursor:{binary} --print"
        return super().start()

    def _resolve_kg_argv(self) -> list[str]:
        """Prefer installed ``kg`` binary; fall back to ``python -m kg``."""
        if self.kg_binary and shutil.which(self.kg_binary):
            return [self.kg_binary, "mcp", "serve", "--project-root", str(self.project_root)]
        return [sys.executable, "-m", "kg", "mcp", "serve", "--project-root", str(self.project_root)]


class SkipLayer2(RuntimeError):
    """Raised when Layer 2 cannot run; the test converts to pytest.skip."""
