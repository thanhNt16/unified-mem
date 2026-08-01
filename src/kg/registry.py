from __future__ import annotations
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RegistryEntry:
    sha256: str
    path: str
    source: str
    type: str
    title: str
    ingested_at: str
    extracted: bool = False
    chunks_done: list[int] = field(default_factory=list)
    chunks_failed: dict[str, str] = field(default_factory=dict)
    chunk_count: int = 0

    @property
    def status(self) -> str:
        completed = len(self.chunks_done) + len(self.chunks_failed)
        if self.chunk_count and completed == self.chunk_count:
            return "failed" if not self.chunks_done else "extracted"
        return "partial" if completed else "pending"


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _iter_records(self):
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)

    def has(self, sha256: str) -> bool:
        return any(r["sha256"] == sha256 for r in self._iter_records())

    def get(self, sha256: str) -> RegistryEntry | None:
        for r in self._iter_records():
            if r["sha256"] == sha256:
                return RegistryEntry(**r)
        return None

    def all(self) -> list[RegistryEntry]:
        return [RegistryEntry(**r) for r in self._iter_records()]

    def unextracted(self) -> list[RegistryEntry]:
        return [e for e in self.all() if not e.extracted]

    def append(self, entry: RegistryEntry) -> bool:
        if self.has(entry.sha256):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")
        return True

    def add_raw(self, body: str, *, title: str, source_type: str,
                chunk_count: int) -> RegistryEntry:
        sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        entry = RegistryEntry(
            sha256=sha256, path="", source="", type=source_type, title=title,
            ingested_at=datetime.now(timezone.utc).isoformat(),
            chunk_count=chunk_count,
        )
        self.append(entry)
        return self.get(sha256) or entry

    def _write(self, entries: list[RegistryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "".join(json.dumps(asdict(e)) + "\n" for e in entries),
            encoding="utf-8",
        )

    def mark_chunk(self, source_sha: str, chunk: int, status: str,
                   reason: str | None = None) -> None:
        if status not in {"done", "failed"}:
            raise ValueError("chunk status must be 'done' or 'failed'")
        entries = self.all()
        for entry in entries:
            if entry.sha256 != source_sha:
                continue
            if entry.chunk_count and not 0 <= chunk < entry.chunk_count:
                raise ValueError(f"chunk {chunk} outside source chunk count")
            entry.chunks_done = [i for i in entry.chunks_done if i != chunk]
            entry.chunks_failed.pop(str(chunk), None)
            if status == "done":
                entry.chunks_done = sorted([*entry.chunks_done, chunk])
            else:
                entry.chunks_failed[str(chunk)] = reason or "failed"
            entry.extracted = entry.status == "extracted"
            self._write(entries)
            return
        raise KeyError(source_sha)

    def mark_extracted(self, sha256: str,
                       chunks_done: list[int], chunks_failed: dict) -> None:
        entries = self.all()
        for entry in entries:
            if entry.sha256 == sha256:
                entry.extracted = True
                entry.chunks_done = chunks_done
                entry.chunks_failed = chunks_failed
        self._write(entries)
