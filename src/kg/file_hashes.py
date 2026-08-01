from __future__ import annotations
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileHashRecord:
    project: str
    rel_path: str
    sha256: str
    mtime_ns: int
    size: int
    kind: str
    extractor_version: str
    chunk_count: int = 0
    chunks_done: tuple[int, ...] = ()
    chunks_failed: dict[str, str] = field(default_factory=dict)
    output_node_ids: tuple[str, ...] = ()
    status: str = "pending"

    @property
    def key(self) -> tuple[str, str]:
        return (self.project, self.rel_path)

    @property
    def content_key(self) -> tuple[str, int, int]:
        return (self.sha256, self.mtime_ns, self.size)


@dataclass(frozen=True)
class FileChanges:
    new: tuple[FileHashRecord, ...] = ()
    unchanged: tuple[FileHashRecord, ...] = ()
    changed: tuple[FileHashRecord, ...] = ()
    deleted: tuple[FileHashRecord, ...] = ()


class FileHashRegistry:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], FileHashRecord] = {}

    def __len__(self) -> int:
        return len(self._records)

    @classmethod
    def load(cls, path: Path) -> FileHashRegistry:
        registry = cls()
        if not path.exists():
            return registry
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rec_dict = json.loads(line)
                rec = FileHashRecord(
                    project=rec_dict["project"],
                    rel_path=rec_dict["rel_path"],
                    sha256=rec_dict["sha256"],
                    mtime_ns=rec_dict["mtime_ns"],
                    size=rec_dict["size"],
                    kind=rec_dict["kind"],
                    extractor_version=rec_dict["extractor_version"],
                    chunk_count=rec_dict.get("chunk_count", 0),
                    chunks_done=tuple(rec_dict.get("chunks_done", [])),
                    chunks_failed=rec_dict.get("chunks_failed", {}),
                    output_node_ids=tuple(rec_dict.get("output_node_ids", [])),
                    status=rec_dict.get("status", "pending"),
                )
                registry._records[rec.key] = rec
        return registry

    def apply(self, changes: FileChanges, extractor_version: str) -> None:
        for rec in (*changes.new, *changes.changed):
            self._records[rec.key] = FileHashRecord(
                **{**rec.__dict__, "extractor_version": extractor_version}
            )
        for rec in changes.deleted:
            self._records.pop(rec.key, None)
        for rec in changes.unchanged:
            self._records[rec.key] = rec

    def write_atomic(self, path: Path) -> None:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        records = sorted(self._records.values(), key=lambda r: (r.project, r.rel_path))
        contents = "\n".join(
            json.dumps({
                "project": r.project, "rel_path": r.rel_path, "sha256": r.sha256,
                "mtime_ns": r.mtime_ns, "size": r.size, "kind": r.kind,
                "extractor_version": r.extractor_version, "chunk_count": r.chunk_count,
                "chunks_done": list(r.chunks_done), "chunks_failed": r.chunks_failed,
                "output_node_ids": list(r.output_node_ids), "status": r.status,
            }, sort_keys=True)
            for r in records
        )
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(contents + ("\n" if contents else ""))
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
        if os.name != "nt":
            try:
                parent_fd = os.open(parent, os.O_RDONLY)
                os.fsync(parent_fd)
                os.close(parent_fd)
            except (OSError, AttributeError):
                pass

    def classify(self, project: str, root: Path) -> FileChanges:
        ignored = {".git", ".kg", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
        new: list[FileHashRecord] = []
        unchanged: list[FileHashRecord] = []
        changed: list[FileHashRecord] = []
        known_paths = set()
        for file_path in scan_paths(root, ignored):
            try:
                rel = file_path.relative_to(root).as_posix()
                if any(part.startswith(".") for part in rel.split("/")):
                    continue
                stat = file_path.stat()
                rec = FileHashRecord(
                    project=project, rel_path=rel,
                    sha256=hashlib.sha256(file_path.read_bytes()).hexdigest(),
                    mtime_ns=stat.st_mtime_ns, size=stat.st_size,
                    kind=_detect_kind(file_path), extractor_version="",
                )
                known_paths.add(rel)
                existing = self._records.get((project, rel))
                if existing is None:
                    new.append(rec)
                elif existing.content_key != rec.content_key:
                    changed.append(rec)
                else:
                    unchanged.append(existing)
            except (OSError, ValueError):
                continue
        deleted = [rec for key, rec in self._records.items()
                   if rec.project == project and key[1] not in known_paths]
        return FileChanges(
            new=tuple(sorted(new, key=lambda r: r.rel_path)),
            unchanged=tuple(sorted(unchanged, key=lambda r: r.rel_path)),
            changed=tuple(sorted(changed, key=lambda r: r.rel_path)),
            deleted=tuple(sorted(deleted, key=lambda r: r.rel_path)),
        )


def scan_paths(root: Path, ignored: set[str] | None = None):
    ignored = ignored or {".git", ".kg", "__pycache__"}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            for item in current.iterdir():
                if item.is_dir() and item.name not in ignored:
                    stack.append(item)
                elif item.is_file():
                    yield item
        except OSError:
            continue


def _detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py": return "python"
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs"}: return "typescript"
    if suffix in {".md", ".markdown"}: return "markdown"
    if suffix == ".txt": return "text"
    if suffix == ".json": return "json"
    if suffix == ".toml": return "toml"
    if suffix in {".yaml", ".yml"}: return "yaml"
    return "unknown"
