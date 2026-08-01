"""Staged atomic rebuild with filesystem writer lock and recovery."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from kg.storage.sqlite import SQLiteAdapter


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@dataclass
class WriterLock:
    root: Path

    @classmethod
    def acquire(cls, root: Path) -> "WriterLock":
        root = Path(root)
        lock_path = root / ".writer.lock"
        try:
            lock_path.mkdir(exist_ok=False)
        except FileExistsError as e:
            meta_file = lock_path / "metadata.json"
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            raise RuntimeError(
                f"writer lock exists at {lock_path} "
                f"(pid={meta.get('pid')}, hostname={meta.get('hostname')}, "
                f"created_at={meta.get('created_at')})"
            ) from e

        meta = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": time.time(),
        }
        (lock_path / "metadata.json").write_text(json.dumps(meta))
        return cls(root=root)

    def release(self) -> None:
        lock_path = self.root / ".writer.lock"
        if lock_path.exists():
            shutil.rmtree(lock_path, ignore_errors=True)


@dataclass
class RebuildCandidate:
    root: Path
    uuid: str
    db_path: Path

    @classmethod
    def create(cls, paths: object, *, incremental: bool = False) -> "RebuildCandidate":
        root = paths.root
        candidate_uuid = str(uuid.uuid4())
        candidate_dir = paths.rebuild_dir(candidate_uuid) if hasattr(paths, "rebuild_dir") else root / f".rebuild-{candidate_uuid}"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        candidate_db = candidate_dir / "kg.db"

        if incremental and paths.kg_db.exists():
            shutil.copy2(paths.kg_db, candidate_db)
        else:
            candidate_db.touch()
        return cls(root=root, uuid=candidate_uuid, db_path=candidate_db)

    def validate(self) -> None:
        try:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result[0] != "ok":
                    raise RuntimeError(f"integrity_check failed: {result[0]}")
            finally:
                conn.close()
        except sqlite3.DatabaseError as e:
            raise RuntimeError(f"integrity_check failed: {self.db_path}") from e

    def publish(self) -> None:
        self.validate()
        with self.db_path.open("rb") as candidate:
            os.fsync(candidate.fileno())
        os.replace(self.db_path, self.root / "kg.db")
        _fsync_directory(self.root)


def rebuild(paths: object, *, fresh: bool = False) -> RebuildCandidate:
    return RebuildCandidate.create(paths, incremental=not fresh)
