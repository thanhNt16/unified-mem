"""Secure Claude Code SessionEnd conversation ingestion."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping
from urllib.parse import urlparse

from kg.config import Config
from kg.conversation import Conversation, format_conversation, parse_conversation
from kg.frontmatter import parse as parse_frontmatter
from kg.paths import KgPaths
from kg.raw_ops import add_source
from kg.registry import Registry, RegistryEntry

MAX_HOOK_INPUT_BYTES = 5 * 1024 * 1024
SUPPORTED_HOOK_EVENTS = frozenset({"SessionEnd"})
SUPPORTED_VERSIONS = frozenset({None, 1, "1", "1.0"})
HARNESS = "claude-code"


class HookInputError(ValueError):
    """Untrusted hook payload failed validation."""


def _read_bounded(stream: BinaryIO, limit: int = MAX_HOOK_INPUT_BYTES) -> bytes:
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise HookInputError("hook input exceeds size limit")
    return raw


def parse_payload(raw: bytes) -> dict[str, Any]:
    if not raw.strip():
        raise HookInputError("empty hook input")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HookInputError("malformed hook JSON") from exc
    if not isinstance(payload, dict):
        raise HookInputError("hook input must be a JSON object")
    event = payload.get("hook_event_name", payload.get("event"))
    if event not in SUPPORTED_HOOK_EVENTS:
        raise HookInputError("unsupported hook event")
    if payload.get("version") not in SUPPORTED_VERSIONS:
        raise HookInputError("unsupported hook protocol version")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
        raise HookInputError("invalid session id")
    return payload


def _records_from_payload(payload: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    for key in ("transcript", "messages", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return [record for record in value if isinstance(record, Mapping)]
    return None


def _allowed_session_root(
    payload: Mapping[str, Any], injected_root: Path | None,
) -> Path | None:
    if injected_root is not None:
        root = Path(injected_root)
    else:
        value = payload.get("session_root") or os.environ.get("KG_HOOK_SESSION_ROOT")
        if not isinstance(value, str) or not value:
            return None
        root = Path(value)
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise HookInputError("invalid session root") from exc
    if not resolved.is_dir():
        raise HookInputError("invalid session root")
    return resolved


def _safe_transcript_path(value: Any, root: Path) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise HookInputError("invalid transcript path")
    parsed = urlparse(value)
    candidate = Path(value)
    if parsed.scheme or parsed.netloc or ".." in candidate.parts:
        raise HookInputError("unsafe transcript path")
    path = candidate if candidate.is_absolute() else root / candidate
    if path.is_symlink():
        raise HookInputError("transcript symlink refused")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise HookInputError("transcript path escapes session root") from exc
    if not resolved.is_file():
        raise HookInputError("transcript is not a file")
    return resolved


def _transcript_source(
    payload: Mapping[str, Any], *, session_root: Path | None,
) -> list[Mapping[str, Any]] | bytes:
    records = _records_from_payload(payload)
    if records is not None:
        return records
    value = payload.get("transcript_path")
    root = _allowed_session_root(payload, session_root)
    if root is None:
        raise HookInputError("transcript records required; path reads need a session root")
    path = _safe_transcript_path(value, root)
    data = path.read_bytes()
    if len(data) > MAX_HOOK_INPUT_BYTES:
        raise HookInputError("transcript exceeds size limit")
    return data


def _flatten_record(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Adapt Claude JSONL envelopes without serializing hidden/tool fields."""
    current: Any = record
    for key in ("message", "event", "data"):
        nested = current.get(key) if isinstance(current, Mapping) else None
        if isinstance(nested, Mapping) and (
            "role" in nested or "message" in nested or "content" in nested
        ):
            current = nested
            if isinstance(current.get("message"), Mapping):
                current = current["message"]
            break
    if not isinstance(current, Mapping):
        return None
    role = current.get("role")
    if role not in {"user", "assistant"}:
        kind = current.get("type")
        if kind in {"user", "assistant"}:
            role = kind
    if role not in {"user", "assistant"}:
        return None
    return {"role": role, "content": current.get("content")}


def conversation_from_payload(
    payload: Mapping[str, Any], *, session_root: Path | None = None,
) -> Conversation:
    source = _transcript_source(payload, session_root=session_root)
    if isinstance(source, bytes):
        records: list[Mapping[str, Any]] = []
        malformed = 0
        for line in source.decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(item, Mapping):
                flattened = _flatten_record(item)
                if flattened is not None:
                    records.append(flattened)
        conversation = parse_conversation(records)
        if malformed:
            meta = asdict(conversation.metadata)
            meta["malformed"] += malformed
            from kg.conversation import ConversationMetadata
            conversation = Conversation(
                conversation.messages, ConversationMetadata(**meta), conversation.content_hash
            )
        return conversation
    return parse_conversation(
        [adapted for item in source if (adapted := _flatten_record(item)) is not None]
    )


def _safe_title(payload: Mapping[str, Any], conversation: Conversation) -> str:
    title = payload.get("title")
    if isinstance(title, str):
        title = " ".join(title.replace("\x00", "").split())[:120]
    if not title and conversation.messages:
        title = " ".join(conversation.messages[0].content.split())[:80]
    return title or "Claude conversation"


def _identity(payload: Mapping[str, Any], conversation: Conversation) -> str:
    return f"{HARNESS}:{payload['session_id']}:{conversation.content_hash}"


@contextmanager
def _ingest_lock(paths: KgPaths) -> Iterator[None]:
    import fcntl

    lock = paths.root / "logs" / "ingest.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("conversation ingest busy") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _ingest_records(paths: KgPaths) -> list[dict[str, Any]]:
    path = paths.root / "logs" / "ingest.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _append_ingest(paths: KgPaths, record: Mapping[str, Any]) -> None:
    path = paths.root / "logs" / "ingest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _repair_registry(paths: KgPaths, content_hash: str) -> str | None:
    registry = Registry(paths.registry)
    marker = f"hash: {content_hash}"
    for raw_path in paths.raw_conversations.glob("*.md"):
        try:
            text = raw_path.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(text)
        except (OSError, ValueError, KeyError):
            continue
        if marker not in text:
            continue
        rel = raw_path.relative_to(paths.root).as_posix()
        existing = registry.get(fm.sha256)
        if existing is None:
            registry.append(RegistryEntry(
                sha256=fm.sha256, path=rel, source=fm.source, type=fm.type,
                title=fm.title, ingested_at=fm.ingested_at,
            ))
        return existing.path if existing else rel
    return None


def ingest_payload(
    project_root: Path,
    payload: Mapping[str, Any],
    *,
    session_root: Path | None = None,
) -> tuple[bool, str | None]:
    root = Path(project_root).expanduser().resolve(strict=True)
    paths = KgPaths.for_cwd(root)
    config = Config.from_path(paths.config)
    if not config.dream.auto_hook:
        return False, None
    conversation = conversation_from_payload(payload, session_root=session_root)
    if not conversation.messages:
        return False, None
    identity = _identity(payload, conversation)
    title = _safe_title(payload, conversation)
    markdown = format_conversation(
        conversation, session_id=str(payload["session_id"]), harness=HARNESS, title=title
    )
    with _ingest_lock(paths):
        records = _ingest_records(paths)
        if any(record.get("identity") == identity and record.get("status") == "committed" for record in records):
            return False, next(
                (record.get("path") for record in reversed(records) if record.get("identity") == identity),
                None,
            )
        repaired = _repair_registry(paths, conversation.content_hash)
        if repaired:
            _append_ingest(paths, {
                "identity": identity, "content_hash": conversation.content_hash,
                "harness": HARNESS, "session_id": payload["session_id"],
                "path": repaired, "status": "committed",
            })
            return False, repaired
        _append_ingest(paths, {
            "identity": identity, "content_hash": conversation.content_hash,
            "harness": HARNESS, "session_id": payload["session_id"],
            "status": "pending",
        })
        created, rel = add_source(
            paths, config, markdown, type="conversation", title=title, conversation=True
        )
        _append_ingest(paths, {
            "identity": identity, "content_hash": conversation.content_hash,
            "harness": HARNESS, "session_id": payload["session_id"],
            "path": rel, "status": "committed",
        })
        return created, rel


def run(
    project_root: Path,
    *,
    stdin: BinaryIO | None = None,
    stderr=None,
    session_root: Path | None = None,
) -> int:
    stdin = stdin or sys.stdin.buffer
    stderr = stderr or sys.stderr
    try:
        payload = parse_payload(_read_bounded(stdin))
        created, path = ingest_payload(project_root, payload, session_root=session_root)
        if path is None:
            print("kg hook: automatic conversation ingest disabled", file=stderr)
        elif created:
            print("kg hook: conversation ingested", file=stderr)
        else:
            print("kg hook: conversation already ingested", file=stderr)
        return 0
    except HookInputError as exc:
        print(f"kg hook: {exc}", file=stderr)
        return 2
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        print(f"kg hook: project unavailable ({type(exc).__name__})", file=stderr)
        return 3
    except Exception as exc:
        print(f"kg hook: ingestion failed ({type(exc).__name__})", file=stderr)
        return 1


__all__ = [
    "MAX_HOOK_INPUT_BYTES", "HookInputError", "parse_payload",
    "conversation_from_payload", "ingest_payload", "run",
]
