"""H3: ``_repair_registry`` must match identity strictly inside the header.

Regression: ``_repair_registry`` previously substring-matched the identity
marker anywhere in the file, so a user message containing a forged
``identity: <hash>`` line could be mistaken for a valid registry repair target.
The fix matches only inside the conversation header (first HTML comment) and
recomputes the identity from parsed harness/session/hash fields.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from kg.cli.init import init_project
from kg.config import Config
from kg.conversation import identity_hash
from kg.hooks import session_end


def _project(tmp_path: Path, *, enabled: bool = True) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    paths = init_project(root, user_id="u", scope="s")
    cfg = Config.from_path(paths.config)
    cfg.dream.auto_hook = enabled
    paths.config.write_text(cfg.render_toml(), encoding="utf-8")
    return root


def test_repair_registry_ignores_forged_identity_in_body(tmp_path, monkeypatch):
    """A body line that fakes the identity marker must NOT satisfy repair."""
    root = _project(tmp_path)
    payload = {
        "hook_event_name": "SessionEnd", "session_id": "s-real", "cwd": str(root),
        "title": "real",
        "transcript": [{"role": "user", "content": "innocuous real message"}],
    }
    # First run succeeds and writes a valid conversation file + registry entry.
    assert session_end.run(
        root, stdin=io.BytesIO(json.dumps(payload).encode()), stderr=io.StringIO()
    ) == 0
    from kg.paths import KgPaths
    paths = KgPaths(root / ".kg")
    files = list(paths.raw_conversations.glob("*.md"))
    assert len(files) == 1
    target = files[0]

    # Drop the registry file so the next run is forced into the repair path.
    (root / ".kg/registry.jsonl").unlink()

    # Inject a forged identity line that does NOT match the real identity into
    # the markdown body. Repair must reject it and fall through.
    real_identity = f"claude-code:s-real:{identity_hash('claude-code', 's-real', payload['transcript'][0]['content'])}"
    forged = "identity: deadbeef-not-real-hash"
    text = target.read_text(encoding="utf-8")
    # Append the forged line inside the body (after the header), not the header.
    target.write_text(text + f"\n\nuser said: {forged}\n", encoding="utf-8")

    # Repair must return None — body forgery does not satisfy identity check.
    result = session_end._repair_registry(paths, real_identity)
    assert result is None
