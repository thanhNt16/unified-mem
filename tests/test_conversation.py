"""Conversation transcript formatter tests."""
from __future__ import annotations

from hashlib import sha256
import json
import pytest
from kg import conversation as cv


SIMPLE = [
    {"role": "user", "content": "hi", "ts": "2026-07-26T10:00:00Z"},
    {"role": "assistant", "content": "hello", "ts": "2026-07-26T10:00:01Z"},
]
NESTED = [
    {"role": "user", "content": [{"type": "text", "text": "nested hello"}]},
    {"role": "assistant", "content": [{"type": "text", "text": "nested reply"}]},
]
MIXED_BLOCKS = [
    {"role": "user", "content": [{"type": "text", "text": "visible"}, {"type": "tool_use", "name": "x", "input": {}}]},
]


def _hash(*pairs: str) -> str:
    body = "\n".join(p for pair in pairs for p in (pair,)) + "\n" if False else "\n".join([seg for role, text in [(pairs[i], pairs[i+1]) for i in range(0, len(pairs), 2)] for seg in (role, text, "")])
    return sha256(body.encode()).hexdigest()


# --- Format / parsing basics -------------------------------------------------

def test_format_basic():
    md = cv.format_conversation(SIMPLE, session_id="sess-1", harness="claude-code")
    assert "**user**" in md and "**assistant**" in md
    assert "hi" in md and "hello" in md
    assert "sess-1" in md and "claude-code" in md
    assert md.endswith("\n")


def test_parse_returns_conversation():
    conv = cv.parse_conversation(SIMPLE)
    assert len(conv.messages) == 2
    assert conv.messages[0].role == "user"
    assert conv.messages[1].content == "hello"


def test_nested_content_blocks():
    conv = cv.parse_conversation(NESTED)
    assert conv.messages[0].content == "nested hello"
    assert conv.messages[1].content == "nested reply"


def test_jsonl_parsing():
    line1 = json.dumps({"role": "user", "content": "from jsonl"})
    line2 = json.dumps({"role": "assistant", "content": "ok"})
    conv = cv.parse_conversation(f"{line1}\n{line2}")
    assert [m.content for m in conv.messages] == ["from jsonl", "ok"]


def test_jsonl_bytes_input():
    payload = (json.dumps({"role": "user", "content": "b"}) + "\n").encode()
    conv = cv.parse_conversation(payload)
    assert conv.messages[0].content == "b"


# --- Malformed / partial / unsupported --------------------------------------

def test_malformed_line_counted_not_raised():
    payload = json.dumps({"role": "user", "content": "good"}) + "\n{bad json\n"
    conv = cv.parse_conversation(payload)
    assert conv.metadata.malformed == 1
    assert [m.content for m in conv.messages] == ["good"]


def test_unsupported_roles_skipped():
    records = [
        {"role": "system", "content": "sys"},
        {"role": "developer", "content": "dev"},
        {"role": "tool", "content": "tool result"},
        {"role": "user", "content": "kept"},
    ]
    conv = cv.parse_conversation(records)
    assert [m.content for m in conv.messages] == ["kept"]
    assert conv.metadata.skipped == 3


def test_missing_ts_tolerated():
    conv = cv.parse_conversation([{"role": "user", "content": "no ts"}])
    assert conv.messages[0].content == "no ts"


def test_partial_message_skipped():
    conv = cv.parse_conversation([{"role": "user"}, {"role": "user", "content": "ok"}])
    assert [m.content for m in conv.messages] == ["ok"]
    assert conv.metadata.skipped == 1


def test_empty_content_skipped():
    conv = cv.parse_conversation([{"role": "user", "content": "   "}])
    assert conv.messages == ()
    assert conv.metadata.skipped == 1


def test_non_text_blocks_skipped():
    conv = cv.parse_conversation(MIXED_BLOCKS)
    assert conv.messages[0].content == "visible"


def test_non_list_non_string_content_skipped():
    conv = cv.parse_conversation([{"role": "user", "content": {"foo": "bar"}}, {"role": "user", "content": 42}, {"role": "user", "content": "kept"}])
    assert [m.content for m in conv.messages] == ["kept"]
    assert conv.metadata.skipped == 2


# --- Secret redaction --------------------------------------------------------

def test_secret_redaction_bearer():
    conv = cv.parse_conversation([{"role": "user", "content": "Authorization: Bearer sk-ant-api01-abc123"}])
    assert "sk-ant-api01-abc123" not in conv.messages[0].content
    assert cv._REDACTION_MARKER in conv.messages[0].content
    assert conv.metadata.redacted >= 1


def test_secret_redaction_api_key_cookie():
    conv = cv.parse_conversation([{"role": "user", "content": "api_key=ABCDEF cookie: session=xyz"}])
    content = conv.messages[0].content
    assert "ABCDEF" not in content
    assert "xyz" not in content
    assert conv.metadata.redacted >= 2


def test_secret_redaction_private_key_block():
    pk = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAI\n-----END RSA PRIVATE KEY-----"
    conv = cv.parse_conversation([{"role": "user", "content": pk}])
    assert "MIIEpAI" not in conv.messages[0].content
    assert conv.metadata.redacted >= 1


def test_secret_redaction_env_line():
    conv = cv.parse_conversation([{"role": "user", "content": "export ANTHROPIC_API_KEY=sk-abc123"}])
    assert "sk-abc123" not in conv.messages[0].content


def test_custom_redaction_patterns():
    import re
    custom = (re.compile(r"PROJECT-\d+"),)
    conv = cv.parse_conversation([{"role": "user", "content": "PROJECT-42"}], secret_patterns=custom)
    assert "PROJECT-42" not in conv.messages[0].content
    assert conv.metadata.redacted == 1


def test_redaction_deterministic():
    a = cv.parse_conversation([{"role": "user", "content": "Bearer token-12345abcde"}])
    b = cv.parse_conversation([{"role": "user", "content": "Bearer token-12345abcde"}])
    assert a.messages[0].content == b.messages[0].content


# --- Canonical hash stability -----------------------------------------------

def test_hash_stable_across_timestamps():
    a = cv.parse_conversation([
        {"role": "user", "content": "hi", "ts": "2026-07-26T10:00:00Z"},
        {"role": "assistant", "content": "hello", "ts": "2026-07-26T10:00:01Z"},
    ])
    b = cv.parse_conversation([
        {"role": "user", "content": "hi", "ts": "2026-07-27T10:00:00Z", "event_id": "evt-999"},
        {"role": "assistant", "content": "hello", "ts": "2026-07-27T10:00:05Z", "event_id": "evt-1000"},
    ])
    assert a.content_hash == b.content_hash


def test_hash_changes_on_text_change():
    a = cv.parse_conversation([{"role": "user", "content": "hi"}])
    b = cv.parse_conversation([{"role": "user", "content": "hi there"}])
    assert a.content_hash != b.content_hash


def test_hash_independent_of_skipped_roles():
    a = cv.parse_conversation([{"role": "user", "content": "hi"}])
    b = cv.parse_conversation([
        {"role": "system", "content": "noise"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "noise2"},
    ])
    assert a.content_hash == b.content_hash


def test_canonical_body_helper():
    body1 = cv.canonical_body([cv.ConversationMessage("user", "hi"), cv.ConversationMessage("assistant", "hello")])
    body2 = "user\nhi\n\nassistant\nhello\n"
    assert body1 == body2


def test_hash_excludes_volatile_keys():
    raw = [{"role": "user", "content": [{"type": "text", "text": "hi"}], "event_id": "e1"}]
    a = cv.parse_conversation(raw)
    raw2 = [{"role": "user", "content": [{"type": "text", "text": "hi"}], "event_id": "totally_different"}]
    b = cv.parse_conversation(raw2)
    assert a.content_hash == b.content_hash


# --- Bounds ------------------------------------------------------------------

def test_oversized_content_truncated():
    big = "x" * (cv.DEFAULT_MAX_CONTENT_CHARS + 50)
    conv = cv.parse_conversation([{"role": "user", "content": big}])
    assert len(conv.messages[0].content) == cv.DEFAULT_MAX_CONTENT_CHARS
    assert conv.metadata.truncated == 1


def test_truncation_deterministic():
    big = "abcdefgh" * 5000
    a = cv.parse_conversation([{"role": "user", "content": big}])
    b = cv.parse_conversation([{"role": "user", "content": big}])
    assert a.messages[0].content == b.messages[0].content
    assert a.content_hash == b.content_hash


def test_oversized_input_rejected():
    with pytest.raises(ValueError):
        cv.parse_conversation("x" * 10, max_input_bytes=5)


def test_max_messages_caps_output():
    records = [{"role": "user", "content": str(i)} for i in range(5)]
    conv = cv.parse_conversation(records, max_messages=2)
    assert len(conv.messages) == 2
    assert conv.metadata.skipped == 3


def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        cv.parse_conversation([], max_input_bytes=0)
    with pytest.raises(ValueError):
        cv.parse_conversation([], max_messages=0)
    with pytest.raises(ValueError):
        cv.parse_conversation([], max_content_chars=0)


# --- No leakage / safe markdown ---------------------------------------------

def test_no_tool_content_leakage():
    conv = cv.parse_conversation([
        {"role": "user", "content": [{"type": "tool_result", "content": "secret data"}]},
        {"role": "user", "content": "clean"},
    ])
    assert all("secret data" not in m.content for m in conv.messages)
    assert conv.messages[0].content == "clean"


def test_markdown_injection_inert():
    conv = cv.parse_conversation([{"role": "user", "content": "<script>alert(1)</script>"}])
    md = cv.format_conversation(conv, session_id="s", harness="h")
    assert "<script>" in md  # preserved literally — four-space indent prevents execution
    header_line = md.splitlines()[0]
    assert header_line.startswith("<!--") and header_line.endswith("-->")


def test_metadata_html_escaped():
    md = cv.format_conversation(SIMPLE, session_id='<x>"&', harness="h")
    first = md.splitlines()[0]
    assert "<x>" not in first  # escaped
    assert "&lt;x&gt;" in first


def test_newlines_in_metadata_escaped():
    md = cv.format_conversation(SIMPLE, session_id="line1\nline2", harness="h")
    header = md.splitlines()[0]
    assert header.count("\n") == 0  # newline stripped from metadata
    assert "line1" in header and "line2" in header


def test_metadata_counts_in_header():
    records = [{"role": "system", "content": "x"}, {"role": "user", "content": "ok"}]
    md = cv.format_conversation(records, session_id="s", harness="h")
    assert "metadata:" in md
    assert '"skipped":1' in md


# --- Additional edge cases ---------------------------------------------------

def test_empty_input():
    conv = cv.parse_conversation([])
    assert conv.messages == ()
    assert conv.metadata == cv.ConversationMetadata()
    h = sha256(b"").hexdigest()
    assert conv.content_hash == h


def test_empty_string_input():
    conv = cv.parse_conversation("")
    assert conv.messages == ()


def test_only_whitespace_lines():
    conv = cv.parse_conversation("   \n\n  \n")
    assert conv.messages == ()
    assert conv.metadata.malformed == 0


def test_carriage_returns_normalized():
    conv = cv.parse_conversation([{"role": "user", "content": "line1\r\nline2\r"}])
    assert conv.messages[0].content == "line1\nline2"


def test_default_secret_patterns_tuple():
    assert isinstance(cv.DEFAULT_SECRET_PATTERNS, tuple)
    assert len(cv.DEFAULT_SECRET_PATTERNS) >= 4


def test_message_order_preserved():
    records = [{"role": role, "content": role} for role in ["user", "assistant", "user", "assistant"]]
    conv = cv.parse_conversation(records)
    assert [m.role for m in conv.messages] == ["user", "assistant", "user", "assistant"]


def test_role_allowlist_strict():
    assert cv.parse_conversation([{"role": "USER", "content": "x"}]).metadata.skipped == 1
    assert cv.parse_conversation([{"role": "User", "content": "x"}]).metadata.skipped == 1


def test_hash_zero_messages_consistent():
    a = cv.parse_conversation([])
    b = cv.parse_conversation([{"role": "system", "content": "ignored"}])
    assert a.content_hash == b.content_hash
