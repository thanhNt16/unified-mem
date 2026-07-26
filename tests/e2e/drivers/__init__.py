"""Concrete SessionDriver implementations for the E2E harness.

Each driver spawns one harness binary (claude, cursor, etc.) and feeds it
natural-language messages. Layer-2 E2E tests use these drivers; Layer-1
deterministic tests shell out to `kg` directly and never touch a driver.
"""
from tests.e2e.drivers.claude_driver import ClaudeDriver

__all__ = ["ClaudeDriver"]
