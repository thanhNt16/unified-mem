"""Secure Claude Code SessionEnd conversation ingestion."""
from __future__ import annotations

import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping
from urllib.parse import urlparse

from kg.config import Config
from kg.conversation import (
    DEFAULT_SECRET_PATTERNS,
    MAX_ENVELOPE_DEPTH,
    MAX_ENVELOPE_RECORDS,
    Conversation,
    format_conversation,
    identity_hash,
    parse_conversation,
    redact_secrets,
)
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
            if len(value) > MAX_ENVELOPE_RECORDS:
                raise HookInputError(f"transcript record count exceeds limit ({MAX_ENVELOPE_RECORDS})")
            return [record for record in value if isinstance(record, Mapping)]
    return None


def _allowed_session_root(
    payload: Mapping[str, Any], injected_root: Path | None,
    *, project_root: Path | None = None,
) -> Path | None:
    """Resolve only an operator-provided root; payload values are assertions.

    If ``project_root`` is given, the injected root must be contained inside it
    (after resolution; symlinks refused) so a compromised harness cannot direct
    reads outside the project tree.
    """
    trusted = injected_root or os.environ.get("KG_HOOK_SESSION_ROOT")
    if trusted is None:
        return None
    try:
        resolved = Path(trusted).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HookInputError("invalid trusted session root") from exc
    if not resolved.is_dir():
        raise HookInputError("invalid trusted session root")
    if project_root is not None:
        try:
            resolved.relative_to(project_root)
        except ValueError as exc:
            raise HookInputError("trusted session root escapes project root") from exc

    declared = payload.get("session_root")
    if declared is not None:
        if not isinstance(declared, str) or not declared:
            raise HookInputError("invalid payload session root")
        try:
            payload_root = Path(declared).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise HookInputError("invalid payload session root") from exc
        if payload_root != resolved:
            raise HookInputError("payload session root does not match trusted root")
    return resolved


def _payload_belongs_to_project(payload: Mapping[str, Any], project_root: Path) -> bool:
    value = payload.get("cwd")
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    try:
        cwd = Path(value).expanduser().resolve(strict=True)
        cwd.relative_to(project_root)
    except (OSError, RuntimeError, ValueError):
        return False
    return cwd.is_dir()


def _safe_transcript_path(value: Any, root: Path) -> Path:
    """Validate a payload-provided transcript path; reject symlinks/traversal.

    Note: callers that actually open the file MUST use ``_open_transcript_fd`` to
    avoid a TOCTOU window between this check and the subsequent read.
    """
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise HookInputError("invalid transcript path")
    parsed = urlparse(value)
    candidate = Path(value)
    if parsed.scheme or parsed.netloc or ".." in candidate.parts:
        raise HookInputError("unsafe transcript path")
    path = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise HookInputError("transcript path escapes session root") from exc
    return resolved


def _open_transcript_fd(path: Path, *, limit: int = MAX_HOOK_INPUT_BYTES) -> tuple[int, int]:
    """Open the transcript with O_NOFOLLOW and return (fd, size) atomically.

    Single ``os.open(O_NOFOLLOW | O_RDONLY | O_NOFOLLOW)`` + ``os.fstat`` on the
    same fd eliminates the TOCTOU window between ``stat`` and ``open``: an
    attacker cannot swap the path for a symlink between the check and the read.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        # symlink → ELOOP under O_NOFOLLOW; treat all open errors as input errors
        raise HookInputError("transcript unavailable") from exc
    try:
        info = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        raise HookInputError("transcript unavailable") from exc
    import stat as _stat
    if not _stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise HookInputError("transcript is not a regular file")
    if info.st_size > limit:
        os.close(fd)
        raise HookInputError("transcript exceeds size limit")
    return fd, info.st_size


def _read_transcript(path: Path, *, limit: int = MAX_HOOK_INPUT_BYTES) -> bytes:
    """Read up to ``limit`` bytes via a single O_NOFOLLOW open (no TOCTOU)."""
    fd, _size = _open_transcript_fd(path, limit=limit)
    try:
        chunks: list[bytes] = []; total = 0
        while total < limit + 1:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk); total += len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise HookInputError("transcript exceeds size limit")
    return data


class PathModeWithoutRoot(Exception):
    """Signal: payload had transcript_path but no trusted session root (graceful no-op)."""


def _transcript_source(
    payload: Mapping[str, Any], *, session_root: Path | None,
    project_root: Path | None = None,
) -> list[Mapping[str, Any]] | bytes:
    records = _records_from_payload(payload)
    has_path_metadata = "session_root" in payload or "transcript_path" in payload
    root = (
        _allowed_session_root(payload, session_root, project_root=project_root)
        if has_path_metadata or records is None
        else None
    )
    path = None
    if "transcript_path" in payload:
        if root is None:
            raise PathModeWithoutRoot("transcript path provided without a trusted session root")
        path = _safe_transcript_path(payload["transcript_path"], root)
    if records is not None:
        return records
    if path is None:
        raise HookInputError("transcript records required; path reads need a trusted session root")
    try:
        data = _read_transcript(path, limit=MAX_HOOK_INPUT_BYTES)
    except HookInputError:
        raise
    except OSError as exc:
        raise HookInputError("transcript unavailable") from exc
    return data


def _flatten_record(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Adapt bounded Claude JSONL envelopes without exposing hidden/tool fields."""
    current: Any = record
    for depth in range(MAX_ENVELOPE_DEPTH + 1):
        if not isinstance(current, Mapping):
            return None
        role = current.get("role")
        if role in {"user", "assistant"}:
            break
        nested = next(
            (
                current[key]
                for key in ("message", "event", "data")
                if isinstance(current.get(key), Mapping)
            ),
            None,
        )
        if nested is None:
            break
        if depth >= MAX_ENVELOPE_DEPTH:
            raise HookInputError(f"transcript envelope exceeds depth limit ({MAX_ENVELOPE_DEPTH})")
        current = nested
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
    project_root: Path | None = None,
) -> Conversation:
    source = _transcript_source(payload, session_root=session_root, project_root=project_root)
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
    if title:
        # Redact secrets BEFORE any downstream truncation/escape/slug/header writes.
        title, _ = redact_secrets(title, DEFAULT_SECRET_PATTERNS)
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


_HEADER_FIELD_RE = re.compile(
    r"\b(?:session|hash|harness|identity):\s*[^|]+\s*(?:\||$)"
)


def _parse_conversation_header(text: str) -> dict[str, str] | None:
    """Extract the structured header (first HTML comment) of a conversation md.

    Returns parsed ``key: value`` fields scoped strictly to that header block,
    or ``None`` if no header is present. Body content is never parsed here.
    """
    m = re.match(r"^<!--\s*(.*?)\s*-->", text, re.DOTALL)
    if not m:
        return None
    header = m.group(1)
    fields: dict[str, str] = {}
    for field in _HEADER_FIELD_RE.findall(header):
        key, _, value = field.partition(":")
        fields[key.strip()] = value.strip(" |")
    return fields


def _repair_registry(paths: KgPaths, identity: str) -> str | None:
    registry = Registry(paths.registry)
    harness, session_id, content_hash = identity.split(":", 2)
    expected_marker = f"identity: {identity_hash(harness, session_id, content_hash)}"
    for raw_path in paths.raw_conversations.glob("*.md"):
        try:
            text = raw_path.read_text(encoding="utf-8")
            fm, _body = parse_frontmatter(text)
        except (OSError, ValueError, KeyError):
            continue
        header = _parse_conversation_header(text)
        if not header:
            continue
        # Recompute identity strictly from parsed header fields; never trust a
        # forged `identity:` line embedded in the markdown body.
        fm_harness = header.get("harness")
        fm_session = header.get("session")
        fm_hash = header.get("hash")
        if not (fm_harness and fm_session and fm_hash):
            continue
        recomputed = f"identity: {identity_hash(fm_harness, fm_session, fm_hash)}"
        if recomputed != expected_marker:
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
    conversation = conversation_from_payload(
        payload, session_root=session_root, project_root=root
    )
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
        repaired = _repair_registry(paths, identity)
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
        root = Path(project_root).expanduser().resolve(strict=True)
        if not _payload_belongs_to_project(payload, root):
            print("kg hook: session outside installed project; skipped", file=stderr)
            return 0
        created, path = ingest_payload(root, payload, session_root=session_root)
        if path is None:
            print("kg hook: automatic conversation ingest disabled", file=stderr)
        elif created:
            print("kg hook: conversation ingested", file=stderr)
        else:
            print("kg hook: conversation already ingested", file=stderr)
        return 0
    except PathModeWithoutRoot as exc:
        print(f"kg hook: {exc}; skipped", file=stderr)
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
    "MAX_HOOK_INPUT_BYTES", "HookInputError", "PathModeWithoutRoot", "parse_payload",
    "conversation_from_payload", "ingest_payload", "run",
]
