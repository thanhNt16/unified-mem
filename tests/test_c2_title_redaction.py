"""C2: title secret redaction before truncation/escape/slug/header.

Regression: secrets in payload.title must never reach the markdown header, the
frontmatter, the registry, or the on-disk filename. The redactor must run BEFORE
any truncation/escape/slug operation that could split the secret across a
boundary and leave a fragment intact.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from kg.cli.init import init_project
from kg.config import Config
from kg.hooks import session_end


def _project(tmp_path: Path, *, enabled: bool = True) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    paths = init_project(root, user_id="u", scope="s")
    cfg = Config.from_path(paths.config)
    cfg.dream.auto_hook = enabled
    paths.config.write_text(cfg.render_toml(), encoding="utf-8")
    return root


def _payload_with_secret_title(secret: str, cwd: Path) -> dict:
    return {
        "hook_event_name": "SessionEnd",
        "session_id": "s-secret-title",
        "cwd": str(cwd),
        "title": f"Discussion about {secret} key",
        "transcript": [
            {"role": "user", "content": "innocuous"},
        ],
    }


@pytest.mark.parametrize("secret", [
    "sk-ant-api03-abcdef1234567890",
    "Bearer token_deadbeefcafe",
    "api_key=AKIAIOSFODNN7EXAMPLE",
])
def test_title_secret_redacted_from_md_header_and_filename(tmp_path, secret):
    root = _project(tmp_path)
    stderr = io.StringIO()
    assert session_end.run(
        root,
        stdin=io.BytesIO(json.dumps(_payload_with_secret_title(secret, root)).encode()),
        stderr=stderr,
    ) == 0
    files = list((root / ".kg/raw/conversations").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    # Secret must not appear anywhere in the rendered conversation markdown
    assert secret not in text
    # Must not appear in the filename
    assert secret not in files[0].name
    # Must not appear in the registry
    registry = (root / ".kg/registry.jsonl").read_text(encoding="utf-8")
    assert secret not in registry
    # Must not appear in ingest log
    log = (root / ".kg/logs/ingest.jsonl").read_text(encoding="utf-8")
    assert secret not in log
    # Must not appear in stderr either
    assert secret not in stderr.getvalue()
