"""ClaudeDriver: concrete SessionDriver for Claude Code CLI.

Spawns `claude -p "<message>" --output-format stream-json` in a subprocess
using the clean env from build_clean_env(). Each `send()` waits for the
process to complete (non-interactive `-p` mode), then parses the
stream-json output and returns the concatenated assistant text.

The driver uses `--bare` to disable hooks/LSP/plugin noise that could
interfere with E2E determinism.

ponytail: no streaming/partial-read; `-p` runs to completion. Add streaming
when testing multi-turn conversations (would need PTY + line-buffer parsing).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.e2e.harness import SessionDriver


class ClaudeDriver(SessionDriver):
    """Drive Claude Code CLI via `claude -p <msg> --output-format stream-json`.

    Each `send()` invocation spawns a fresh `claude -p` process, waits for
    completion, and returns the assistant's concatenated text output.

    The `_spawn` method is a no-op (no persistent process). `_send` does the
    real work — this matches Claude Code's non-interactive `-p` contract.
    """

    def _spawn(self, env: dict[str, str]) -> subprocess.Popen:
        # ClaudeDriver uses `-p` (print mode), which runs to completion.
        # No persistent process to spawn. We store env for _send.
        # Return a dummy "started" sentinel; _send spawns per-call.
        self._claude_env = env
        return subprocess.Popen(
            ["true"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def _send(self, message: str) -> str:
        env = getattr(self, "_claude_env", None)
        if env is None:
            raise RuntimeError("ClaudeDriver not started; call start() first")

        proc = subprocess.Popen(
            [
                "claude",
                "-p", message,
                "--output-format", "stream-json",
                "--bare",
                "--allowed-tools", "mcp__kg__search_memory",
                "--allowed-tools", "mcp__kg__expand_memory",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(self.project_root),
        )
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise TimeoutError(f"claude -p timed out after {self.timeout}s")

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude -p exited {proc.returncode}\n"
                f"stderr={stderr[:500]!r}\n"
                f"stdout={stdout[:500]!r}"
            )

        return self._parse_stream_json(stdout)

    @staticmethod
    def _parse_stream_json(raw: str) -> str:
        """Parse stream-json output, concatenate assistant text content."""
        texts: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # stream-json emits objects with a "type" field.
            obj_type = obj.get("type", "")
            if obj_type == "assistant":
                # The message content is nested.
                for block in obj.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block["text"])
            elif obj_type == "content_block_delta":
                delta = obj.get("delta", {})
                if delta.get("type") == "text_delta":
                    texts.append(delta.get("text", ""))
        return "".join(texts)
