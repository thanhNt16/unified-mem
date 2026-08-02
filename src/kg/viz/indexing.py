"""Single-flight index job management for kg viz.

Consumes an injected ``runner(root, project_name)``; production wiring calls
the existing kg index command boundary, never a shell.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

_PROJECT_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


@dataclass(slots=True)
class IndexJob:
    slot: int
    path: str
    project_name: str
    status: Literal["indexing", "done", "error"]
    error: str | None
    thread: Thread


class IndexBusy(RuntimeError):
    """A single-flight index job is already running."""


class InvalidProjectPath(ValueError):
    """Root path or project name failed validation."""


class IndexManager:
    def __init__(self, runner: Callable[[Path, str], None]) -> None:
        self._runner = runner
        self._lock = threading.Lock()
        self._jobs: list[IndexJob] = []
        self._active: IndexJob | None = None
        self._next_slot = 0

    def start(self, root_path: str, project_name: str) -> IndexJob:
        root = self._resolve(root_path, project_name)
        with self._lock:
            if self._active is not None:
                raise IndexBusy("an index job is already running")
            job = IndexJob(
                slot=self._next_slot,
                path=str(root),
                project_name=project_name,
                status="indexing",
                error=None,
                thread=threading.Thread(
                    target=self._run, args=(root,), daemon=True, name=f"kg-index-{self._next_slot}"
                ),
            )
            self._next_slot += 1
            self._jobs.append(job)
            self._active = job
        job.thread.start()
        return job

    def status(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {"slot": j.slot, "status": j.status, "path": j.path, "error": j.error}
                for j in self._jobs
            ]

    def _resolve(self, root_path: str, project_name: str) -> Path:
        if not isinstance(root_path, str) or not Path(root_path).expanduser().is_absolute():
            raise InvalidProjectPath("root path must be absolute")
        if not _PROJECT_NAME_RE.fullmatch(project_name):
            raise InvalidProjectPath("project name must match [A-Za-z0-9._-]{1,128}")
        try:
            root = Path(root_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise InvalidProjectPath(f"root path does not resolve to an existing path: {exc}") from exc
        if not root.is_dir():
            raise InvalidProjectPath("root path must be an existing directory")
        return root

    def _run(self, root: Path) -> None:
        with self._lock:
            job = self._active
        assert job is not None
        error: str | None = None
        try:
            self._runner(root, job.project_name)
        except Exception as exc:  # noqa: BLE001 - surfaced via status()
            error = str(exc)
        finally:
            with self._lock:
                job.status = "error" if error is not None else "done"
                job.error = error
                self._active = None
