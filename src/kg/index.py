"""Incremental index planning for source discovery and processing modes."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from kg.file_hashes import FileChanges, FileHashRegistry
from kg.paths import KgPaths


class IndexMode(StrEnum):
    FAST = "fast"
    MODERATE = "moderate"
    FULL = "full"


@dataclass(frozen=True)
class IndexPlan:
    mode: IndexMode
    action: str
    changes: FileChanges
    run_structural: bool
    run_embeddings: bool
    run_document_extraction: bool
    run_similarity: bool
    run_community: bool


@dataclass(frozen=True)
class IndexResult:
    plan: IndexPlan
    indexed: int = 0
    skipped: int = 0
    failed: int = 0


def plan_index(
    paths: KgPaths,
    root: Path,
    mode: str | IndexMode,
    extractor_version: str,
    *,
    incremental_threshold: float = 0.10,
) -> IndexPlan:
    """Create deterministic work plan from file identity state."""
    try:
        selected = IndexMode(mode)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in IndexMode)
        raise ValueError(f"invalid index mode {mode!r}; expected {allowed}") from exc
    if not 0.0 <= incremental_threshold <= 1.0:
        raise ValueError("incremental_threshold must be between 0 and 1")

    registry = FileHashRegistry.load(paths.file_hashes)
    changes = registry.classify(paths.root.name, Path(root))
    touched = len(changes.new) + len(changes.changed) + len(changes.deleted)
    total = touched + len(changes.unchanged)
    action = "incremental" if total == 0 or touched / total <= incremental_threshold else "full_rebuild"

    return IndexPlan(
        mode=selected,
        action=action,
        changes=changes,
        run_structural=True,
        run_embeddings=selected is not IndexMode.FAST,
        run_document_extraction=selected is not IndexMode.FAST,
        run_similarity=selected is IndexMode.FULL,
        run_community=selected is IndexMode.FULL,
    )
