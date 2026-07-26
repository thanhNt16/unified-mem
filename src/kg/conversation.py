"""Safe, bounded conversation transcript parsing and Markdown rendering."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import html
import json
import re
from typing import Any, Iterable, Mapping, Pattern

DEFAULT_MAX_INPUT_BYTES = 2_000_000
DEFAULT_MAX_MESSAGES = 1_000
DEFAULT_MAX_CONTENT_CHARS = 32_000
MAX_ENVELOPE_DEPTH = 10
MAX_ENVELOPE_RECORDS = 10_000
_REDACTION_MARKER = "[REDACTED]"
DEFAULT_SECRET_PATTERNS: tuple[Pattern[str], ...] = (
    # Authorization: Bearer / Proxy-Authorization: Bearer
    re.compile(r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*bearer\s+[^\s,;]+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{8,}"),
    # Authorization: Basic <credentials>
    re.compile(r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*basic\s+[^\s,;]+"),
    # header/shell-style key=value assignments
    re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|password|cookie|set-cookie)\s*[:=]\s*[^\s,;]+"),
    # JSON-quoted secret values: "auth_token":"sk-..." (value side only)
    re.compile(
        r"(?i)(\"(?:api[_ -]?key|auth[_ -]?token|token|secret|password|cookie)\"\s*:\s*\")"
        r"[^\"]{4,}(\")"
    ),
    # Standalone Anthropic / GitHub / Slack / AWS tokens without prefix
    re.compile(r"sk-ant-[a-z0-9._-]{8,}"),
    re.compile(r"gh[opus]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[bpoa]-[0-9a-zA-Z-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # PEM private key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    # ENV-style assignments
    re.compile(r"(?im)^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|COOKIE)\s*=\s*.+$"),
)


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ConversationMetadata:
    malformed: int = 0
    skipped: int = 0
    redacted: int = 0
    truncated: int = 0


@dataclass(frozen=True)
class Conversation:
    messages: tuple[ConversationMessage, ...]
    metadata: ConversationMetadata
    content_hash: str


def redact_secrets(text: str, patterns: Iterable[Pattern[str]] = DEFAULT_SECRET_PATTERNS) -> tuple[str, int]:
    count = 0
    for pattern in patterns:
        text, replaced = pattern.subn(_REDACTION_MARKER, text)
        count += replaced
    return text, count


def parse_conversation(source: str | bytes | Iterable[Mapping[str, Any]], *, max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES, max_messages: int = DEFAULT_MAX_MESSAGES, max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS, secret_patterns: Iterable[Pattern[str]] = DEFAULT_SECRET_PATTERNS) -> Conversation:
    """Parse JSONL or mappings; retain only user/assistant text blocks."""
    if min(max_input_bytes, max_messages, max_content_chars) < 1:
        raise ValueError("limits must be positive")
    records, malformed = _records(source, max_input_bytes)
    messages: list[ConversationMessage] = []
    skipped = redacted = truncated = 0
    seen = 0
    for record in records:
        seen += 1
        if seen > MAX_ENVELOPE_RECORDS:
            raise ValueError(f"conversation record count exceeds limit ({MAX_ENVELOPE_RECORDS})")
        if len(messages) >= max_messages:
            skipped += 1
            continue
        if not isinstance(record, Mapping) or record.get("role") not in {"user", "assistant"}:
            skipped += 1
            continue
        content = _text(record.get("content"))
        if content is None:
            skipped += 1
            continue
        content = _normalize(content)
        if not content:
            skipped += 1
            continue
        if len(content) > max_content_chars:
            content, truncated = content[:max_content_chars], truncated + 1
        content, replacements = redact_secrets(content, secret_patterns)
        redacted += replacements
        messages.append(ConversationMessage(record["role"], content))
    canonical = canonical_body(messages)
    return Conversation(tuple(messages), ConversationMetadata(malformed, skipped, redacted, truncated), sha256(canonical.encode()).hexdigest())


def parse_jsonl(source: str | bytes, **kwargs: Any) -> Conversation:
    return parse_conversation(source, **kwargs)


def canonical_body(messages: Iterable[ConversationMessage | Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role, content = (message.role, message.content) if isinstance(message, ConversationMessage) else (message.get("role"), _text(message.get("content")))
        if role in {"user", "assistant"} and isinstance(content, str):
            lines.extend((role, _normalize(content), ""))
    return "\n".join(lines)


def format_conversation(messages: list[dict[str, Any]] | Conversation, session_id: str, harness: str, *, title: str = "") -> str:
    conversation = messages if isinstance(messages, Conversation) else parse_conversation(messages)
    identity = identity_hash(harness, session_id, conversation.content_hash)
    header = " | ".join(part for part in (f"session: {_safe(session_id)}", f"harness: {_safe(harness)}", f"title: {_safe(title)}" if title else "", f"hash: {conversation.content_hash}", f"identity: {identity}", f"metadata: {json.dumps(asdict(conversation.metadata), sort_keys=True, separators=(',', ':'))}") if part)
    lines = [f"<!-- {header} -->", ""]
    for message in conversation.messages:
        lines.extend((f"### **{message.role}**", "", *[f"    {line}" for line in message.content.splitlines()], ""))
    return "\n".join(lines).rstrip() + "\n"


def _records(source: str | bytes | Iterable[Mapping[str, Any]], limit: int) -> tuple[Iterable[Any], int]:
    if not isinstance(source, (str, bytes)):
        return source, 0
    raw = source.encode() if isinstance(source, str) else source
    if len(raw) > limit:
        raise ValueError("conversation input exceeds max_input_bytes")
    records: list[Any] = []
    malformed = 0
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            malformed += 1
    return records, malformed


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    parts = [block["text"] for block in value if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)]
    return "\n".join(parts) if parts else None


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def identity_hash(harness: str, session_id: str, content_hash: str) -> str:
    return sha256(f"{harness}:{session_id}:{content_hash}".encode()).hexdigest()


def _safe(value: object) -> str:
    return html.escape(str(value).replace("\x00", "").replace("\n", " ").replace("\r", " "), quote=True)
