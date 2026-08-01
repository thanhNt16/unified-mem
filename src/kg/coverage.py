from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CoverageEntry:
    rel_path: str
    status: str
    reason: str | None = None
    outputs: tuple[str, ...] = ()


@dataclass
class CoverageReport:
    generation: int
    mode: str
    entries: list[CoverageEntry] = field(default_factory=list)

    def record(self, rel_path: str, status: str, reason: str | None = None,
               outputs: list[str] | None = None) -> None:
        if status not in {"indexed", "skipped", "failed", "stale"}:
            raise ValueError(f"unknown coverage status: {status}")
        self.entries.append(CoverageEntry(
            rel_path, status, reason, tuple(outputs or ())))

    def body(self) -> dict:
        sources = {key: 0 for key in ("seen", "indexed", "skipped", "failed", "stale")}
        reasons: dict[str, int] = {}
        outputs = {"nodes": 0, "edges": 0}
        for entry in self.entries:
            sources["seen"] += 1
            sources[entry.status] += 1
            if entry.reason:
                reasons[entry.reason] = reasons.get(entry.reason, 0) + 1
            for output in entry.outputs:
                if output.startswith("edge:"):
                    outputs["edges"] += 1
                else:
                    outputs["nodes"] += 1
        return {"generation": self.generation, "mode": self.mode,
                "sources": sources, "by_reason": reasons, "outputs": outputs}

    def write_atomic(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.body(), fh, sort_keys=True, separators=(",", ":"))
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
