"""Install manifest: safe ownership, provenance, atomic persistence.

Records every file the installer owns so uninstall touches only exact
digest-matching fragments. Corrupt/unknown-version manifests fail closed —
the file is preserved untouched and load_manifest raises.

ponytail: no backup-restore yet (callers can read backup_path/sha manually).
Add: restore() helper when rollback policy lands in T3/T4.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Optional
from uuid import uuid4


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Bump on schema change. Old versions fail closed at load.
MANIFEST_VERSION = 1

#: Manifest lives under project_root, not home — installer is project-scoped.
MANIFEST_FILENAME = ".kg-install-manifest.json"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Harness(str, Enum):
    """Closed enum — never interpolate raw harness strings into paths."""

    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    CURSOR = "cursor"
    AGENTS = "agents"


class ArtifactKind(str, Enum):
    OWNED_FILE = "owned_file"
    JSON_OBJECT = "json_object"
    JSON_LIST_MEMBER = "json_list_member"
    MARKER_BLOCK = "marker_block"
    COPIED_SKILL = "copied_skill"


class TransactionState(str, Enum):
    STAGING = "staging"
    COMMITTED = "committed"
    ROLLBACK_NEEDED = "rollback-needed"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstalledArtifact:
    path: str
    kind: ArtifactKind
    content_sha256: str
    backup_path: Optional[str] = None
    backup_sha256: Optional[str] = None
    fingerprint: Optional[str] = None
    prior_value: Optional[str] = None
    ownership_marker: Optional[str] = None
    version: int = 1


@dataclass
class InstallManifest:
    version: int = MANIFEST_VERSION
    project_root: str = ""
    harness: Harness = Harness.CLAUDE
    transaction_id: str = field(default_factory=lambda: uuid4().hex)
    transaction_state: TransactionState = TransactionState.STAGING
    artifacts: list[InstalledArtifact] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # --- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "project_root": self.project_root,
            "harness": self.harness.value,
            "transaction_id": self.transaction_id,
            "transaction_state": self.transaction_state.value,
            "artifacts": [self._artifact_to_dict(a) for a in self.artifacts],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InstallManifest":
        if d.get("version") != MANIFEST_VERSION:
            raise ValueError(
                f"Unsupported manifest version {d.get('version')!r}; "
                f"expected {MANIFEST_VERSION}"
            )
        for key in ("project_root", "harness", "transaction_id",
                    "transaction_state", "created_at", "updated_at"):
            if key not in d:
                raise ValueError(f"Manifest missing required field {key!r}")
        artifacts = [cls._artifact_from_dict(a) for a in d.get("artifacts", [])]
        return cls(
            version=d["version"],
            project_root=d["project_root"],
            harness=Harness(d["harness"]),
            transaction_id=d["transaction_id"],
            transaction_state=TransactionState(d["transaction_state"]),
            artifacts=artifacts,
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )

    @staticmethod
    def _artifact_to_dict(a: InstalledArtifact) -> dict:
        return {
            "path": a.path,
            "kind": a.kind.value,
            "content_sha256": a.content_sha256,
            "backup_path": a.backup_path,
            "backup_sha256": a.backup_sha256,
            "fingerprint": a.fingerprint,
            "prior_value": a.prior_value,
            "ownership_marker": a.ownership_marker,
            "version": a.version,
        }

    @staticmethod
    def _artifact_from_dict(d: dict) -> InstalledArtifact:
        for key in ("path", "kind", "content_sha256"):
            if key not in d:
                raise ValueError(f"Artifact missing required field {key!r}")
        return InstalledArtifact(
            path=d["path"],
            kind=ArtifactKind(d["kind"]),
            content_sha256=d["content_sha256"],
            backup_path=d.get("backup_path"),
            backup_sha256=d.get("backup_sha256"),
            fingerprint=d.get("fingerprint"),
            prior_value=d.get("prior_value"),
            ownership_marker=d.get("ownership_marker"),
            version=d.get("version", 1),
        )

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    # --- idempotency ------------------------------------------------------

    def find(self, path: str) -> Optional[InstalledArtifact]:
        for a in self.artifacts:
            if a.path == path:
                return a
        return None


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _resolve_root(project_root: Path) -> Path:
    """Resolve project_root (no symlink check — caller's root)."""
    return Path(project_root).resolve(strict=False)


def _check_no_symlink_escape(path: Path, root: Path) -> Path:
    """Walk *path* components under *root*; raise on any symlink that leaves.

    Resolves each prefix and confirms the real path stays under the resolved
    root. This catches both intermediate symlinks and a symlinked final hop.
    """
    resolved_root = _resolve_root(root)
    current = resolved_root
    for part in Path(path).parts:
        if part in ("", ".", "/"):
            # Resolve to absolute root on a leading "/" or skip empty parts.
            if part == "/":
                current = Path("/").resolve(strict=False)
            continue
        current = current / part
        try:
            real = current.resolve(strict=False)
        except OSError:
            real = current.absolute()
        try:
            real.relative_to(resolved_root)
        except ValueError:
            raise ValueError(
                f"Path escapes containment root {resolved_root}: "
                f"{current} -> {real}"
            ) from None
    return current


def _assert_contained(path: Path, root: Path) -> Path:
    """Verify resolved *path* stays under *root*. Rejects symlink escapes."""
    resolved_root = _resolve_root(root)
    try:
        real = Path(path).resolve(strict=False)
        real.relative_to(resolved_root)
    except (ValueError, OSError) as exc:
        raise ValueError(
            f"Path {path} escapes containment root {resolved_root}: {exc}"
        ) from None
    return real


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def content_hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return content_hash(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically.

    Uses a same-directory tempfile + fsync + os.replace. Preserves the
    destination's existing mode bits. Raises without touching the original
    file on any failure.
    """
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)

    try:
        existing_mode = stat.S_IMODE(Path(path).stat().st_mode)
    except FileNotFoundError:
        existing_mode = 0o644

    fd = -1
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(parent), prefix=".tmp-", suffix=Path(path).name
        )
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        os.chmod(path, existing_mode)
        tmp = None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Manifest persistence
# ---------------------------------------------------------------------------


def manifest_path(project_root: Path) -> Path:
    """Compute manifest path with symlink containment check."""
    root = _resolve_root(project_root)
    target = Path(project_root) / MANIFEST_FILENAME
    _assert_contained(target, root)
    return target


def save_manifest(manifest: InstallManifest, project_root: Path) -> Path:
    """Persist manifest atomically. Returns the written path."""
    dest = manifest_path(project_root)
    manifest.touch()
    atomic_write(dest, json.dumps(manifest.to_dict(), indent=2).encode("utf-8"))
    return dest


def load_manifest(project_root: Path) -> InstallManifest:
    """Load manifest. Fails closed on missing/corrupt/unknown-version."""
    dest = manifest_path(project_root)
    if not dest.exists():
        raise FileNotFoundError(f"No manifest at {dest}")
    # Reject symlinks and non-regular files entirely — manifest must be a
    # regular file we own.
    if not dest.is_file() or dest.is_symlink():
        raise ValueError(f"Manifest path is not a regular file: {dest}")
    try:
        raw = dest.read_bytes()
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Corrupt manifest at {dest}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Manifest at {dest} is not a JSON object")
    return InstallManifest.from_dict(data)


# ---------------------------------------------------------------------------
# Drift check / uninstall safety
# ---------------------------------------------------------------------------


class DriftConflict(Exception):
    """Raised when an artifact's on-disk state no longer matches the manifest."""

    def __init__(self, path: str, expected: str, actual: str):
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Drift at {path}: expected sha256 {expected[:12]}, "
            f"found {actual[:12]}. Refusing uninstall — inspect the file or "
            f"restore from backup before retrying."
        )


def check_drift(artifact: InstalledArtifact, project_root: Path) -> None:
    """Verify on-disk artifact matches manifest digest + fingerprint.

    Raises DriftConflict on mismatch; the caller must not uninstall.
    """
    target = Path(artifact.path)
    # Containment: artifact path must stay under project root.
    _assert_contained(target, _resolve_root(project_root))
    if not target.exists():
        raise DriftConflict(artifact.path, artifact.content_sha256, "<missing>")
    if target.is_symlink() or not target.is_file():
        raise DriftConflict(
            artifact.path, artifact.content_sha256, "<not a regular file>"
        )
    current = file_hash(target)
    if current != artifact.content_sha256:
        raise DriftConflict(artifact.path, artifact.content_sha256, current)
    if artifact.fingerprint:
        _verify_fingerprint(target, artifact.fingerprint)


def _verify_fingerprint(target: Path, fingerprint: str) -> None:
    """Verify JSON-pointer fingerprint against target's JSON content.

    Fingerprints look like ``/key/0/nested`` — slash-separated path into the
    parsed JSON, with integer segments indexing arrays. Raises DriftConflict
    if the structure doesn't resolve.
    """
    try:
        on_disk = json.loads(target.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DriftConflict(
            str(target), fingerprint, f"<unreadable JSON: {exc}>"
        ) from exc
    node = on_disk
    ptr = fingerprint.lstrip("/")
    parts = ptr.split("/") if ptr else []
    try:
        for p in parts:
            if isinstance(node, list):
                node = node[int(p)]
            else:
                node = node[p]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DriftConflict(
            str(target), fingerprint, f"<fingerprint unresolved: {exc}>"
        ) from exc


def find_artifact(
    manifest: InstallManifest, path: str
) -> Optional[InstalledArtifact]:
    """Idempotent lookup by path."""
    return manifest.find(path)
