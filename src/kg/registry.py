from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
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
    chunks_failed: dict = field(default_factory=dict)


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

    def mark_extracted(self, sha256: str,
                       chunks_done: list[int], chunks_failed: dict) -> None:
        entries = self.all()
        for e in entries:
            if e.sha256 == sha256:
                e.extracted = True
                e.chunks_done = chunks_done
                e.chunks_failed = chunks_failed
        self.path.write_text(
            "".join(json.dumps(asdict(e)) + "\n" for e in entries),
            encoding="utf-8",
        )
