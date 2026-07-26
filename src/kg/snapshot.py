"""WAL-safe snapshot/restore via SQLite backup API + streaming zstd."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import zstandard as zstd

DEFAULT_MAX_BYTES = 10 * 1024 ** 3
_CHUNK_SIZE = 256 * 1024


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _integrity_check(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"integrity_check failed: {path}")
    finally:
        conn.close()


def create_snapshot(db_path: Path, out_path: Path) -> Path:
    """Create a compressed, WAL-consistent SQLite backup atomically."""
    db_path, out_path = Path(db_path), Path(out_path)
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    backup_fd, backup_name = tempfile.mkstemp(dir=out_path.parent, suffix=".db.tmp")
    artifact_fd, artifact_name = tempfile.mkstemp(dir=out_path.parent, suffix=".zst.tmp")
    os.close(backup_fd)
    os.close(artifact_fd)
    backup_path, artifact_path = Path(backup_name), Path(artifact_name)
    try:
        source = sqlite3.connect(str(db_path))
        destination = sqlite3.connect(str(backup_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        _integrity_check(backup_path)
        with backup_path.open("rb") as source:
            with artifact_path.open("wb") as destination, \
                 zstd.ZstdCompressor(level=19).stream_writer(destination) as writer:
                while chunk := source.read(_CHUNK_SIZE):
                    writer.write(chunk)
                writer.flush()
            with artifact_path.open("ab") as destination:
                os.fsync(destination.fileno())
        os.replace(artifact_path, out_path)
        _fsync_directory(out_path.parent)
        return out_path
    finally:
        backup_path.unlink(missing_ok=True)
        artifact_path.unlink(missing_ok=True)


def restore_snapshot(
    snapshot_path: Path,
    db_path: Path,
    *,
    force: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    """Atomically restore a verified artifact, preserving an existing DB on failure."""
    snapshot_path, db_path = Path(snapshot_path), Path(db_path)
    if not snapshot_path.is_file():
        raise FileNotFoundError(snapshot_path)
    if db_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing database: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(dir=db_path.parent, suffix=".db.tmp")
    os.close(temp_fd)
    temp_path = Path(temp_name)
    try:
        written = 0
        with snapshot_path.open("rb") as source, temp_path.open("wb") as destination:
            with zstd.ZstdDecompressor().stream_reader(source) as reader:
                while chunk := reader.read(_CHUNK_SIZE):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(f"Snapshot exceeds maximum decompressed size ({max_bytes} bytes)")
                    destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        _integrity_check(temp_path)
        # Delete stale WAL sidecars only after the replacement has validated.
        for sidecar in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            sidecar.unlink(missing_ok=True)
        os.replace(temp_path, db_path)
        _fsync_directory(db_path.parent)
        return db_path
    finally:
        temp_path.unlink(missing_ok=True)


def snapshot(paths: object) -> Path:
    """Compatibility wrapper for project-local snapshots."""
    paths.snapshots.mkdir(parents=True, exist_ok=True)
    return create_snapshot(paths.kg_db, paths.snapshots / "kg.db.zst")


def restore(paths: object, *, force: bool = False) -> Path:
    """Compatibility wrapper for project-local snapshots."""
    return restore_snapshot(paths.snapshots / "kg.db.zst", paths.kg_db, force=force)
