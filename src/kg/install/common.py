"""Shared safe writer primitives for non-Claude harness installers."""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from .manifest import ArtifactKind, Harness, InstalledArtifact, InstallManifest, TransactionState, _check_no_symlink_escape, atomic_write, content_hash, load_manifest, manifest_path, save_manifest

SKILLS = ("kg-extract", "kg-query", "kg-dream")
MCP_ARGV = ("kg", "mcp", "serve", "--project-root")
_MISSING = {"__kg_install_missing__": True}

# Bundled skills ship inside the installed package so `kg install` works on
# foreign projects without a local `skills/` checkout.
_BUNDLED_SKILLS = Path(__file__).resolve().parent.parent / "skills"


def default_skills_src(project: Path) -> Path:
    """Resolve skills source: explicit arg > <project>/skills > bundled."""
    local = project / "skills"
    if local.is_dir():
        return local
    return _BUNDLED_SKILLS

class InstallConflict(RuntimeError):
    pass

@dataclass(frozen=True)
class ManualStep:
    path: Path
    reason: str
    patch: str

@dataclass(frozen=True)
class PlannedWrite:
    path: Path
    data: bytes
    root: Path
    kind: ArtifactKind
    fingerprint: str | None = None
    prior_value: str | None = None
    ownership_marker: str | None = None

@dataclass(frozen=True)
class PlannedSkill:
    source: Path
    destination: Path
    root: Path

@dataclass
class InstallPlan:
    harness: Harness
    project_root: Path
    home_root: Path
    writes: list[PlannedWrite] = field(default_factory=list)
    skills: list[PlannedSkill] = field(default_factory=list)
    manual_steps: list[ManualStep] = field(default_factory=list)
    existing_manifest: InstallManifest | None = None

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple([w.path for w in self.writes] + [s.destination for s in self.skills])


def validate_roots(project_root: Path, home_root: Path) -> tuple[Path, Path]:
    project = Path(project_root).resolve(strict=True)
    home = Path(home_root).resolve(strict=True)
    if not project.is_dir() or not home.is_dir():
        raise InstallConflict("project_root and home_root must be directories")
    return project, home


def validate_target(path: Path, root: Path) -> None:
    resolved_root = root.resolve(strict=False)
    try:
        path.resolve(strict=False).relative_to(resolved_root)
    except (ValueError, OSError) as exc:
        raise InstallConflict(f"Path escapes allowed root {resolved_root}: {path}") from exc
    current = path
    while True:
        if current.exists() and current.is_symlink():
            raise InstallConflict(f"Symlink target refused: {current}")
        if current == resolved_root:
            break
        if current == current.parent:
            raise InstallConflict(f"Path escapes allowed root {resolved_root}: {path}")
        current = current.parent


def tree_hash(path: Path) -> str:
    digest = sha256()
    for item in sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix()):
        if item.is_symlink():
            raise InstallConflict(f"Symlink in owned tree refused: {item}")
        digest.update(("D\0" if item.is_dir() else "F\0").encode())
        digest.update(item.relative_to(path).as_posix().encode() + b"\0")
        if item.is_file():
            digest.update(item.read_bytes())
    return digest.hexdigest()


def owned_manifest(project: Path, harness: Harness) -> InstallManifest | None:
    path = manifest_path(project)
    if not path.exists():
        return None
    manifest = load_manifest(project)
    if manifest.harness is not harness:
        raise InstallConflict(f"Manifest belongs to {manifest.harness.value}, not {harness.value}")
    if manifest.transaction_state is not TransactionState.COMMITTED:
        raise InstallConflict(f"Incomplete install transaction {manifest.transaction_id}; recover first")
    return manifest


def add_skills(plan: InstallPlan, skills_src: Path, destination_root: Path) -> None:
    source = Path(skills_src).resolve(strict=True)
    if not source.is_dir():
        raise InstallConflict("skills_src must be a directory")
    validate_target(destination_root, plan.project_root)
    for name in SKILLS:
        src, dest = source / name, destination_root / name
        if not src.is_dir() or not (src / "SKILL.md").is_file():
            raise InstallConflict(f"Required skill missing: {src}")
        tree_hash(src); validate_target(dest, plan.project_root)
        if dest.exists():
            if plan.existing_manifest and dest.is_dir() and tree_hash(dest) == tree_hash(src):
                continue
            raise InstallConflict(f"Skill destination already exists: {dest}")
        plan.skills.append(PlannedSkill(src, dest, plan.project_root))


def json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InstallConflict(f"Malformed JSON refused: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallConflict(f"JSON config must be an object: {path}")
    return value


def dump_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def mcp_argv(project: Path) -> list[str]:
    return [*MCP_ARGV, str(project)]


def add_json_mcp(plan: InstallPlan, path: Path, top_key: str, value: dict[str, Any]) -> None:
    validate_target(path, plan.project_root)
    data = json_object(path)
    servers = data.get(top_key, {})
    if not isinstance(servers, dict):
        raise InstallConflict(f"{top_key} must be an object: {path}")
    old = servers.get("kg", _MISSING)
    if old != _MISSING and old != value:
        raise InstallConflict(f"MCP server name collision at 'kg': {path}")
    if old == value:
        if not plan.existing_manifest:
            raise InstallConflict(f"Pre-existing unowned kg MCP entry refused: {path}")
        return
    updated = dict(data); merged = dict(servers); merged["kg"] = value; updated[top_key] = merged
    plan.writes.append(PlannedWrite(path, dump_json(updated), plan.project_root, ArtifactKind.JSON_OBJECT, f"/{top_key}/kg", json.dumps(_MISSING, sort_keys=True)))


def marker_bytes(harness: Harness) -> tuple[str, str, bytes]:
    begin = f"<!-- kg-install:{harness.value}:begin -->"
    end = f"<!-- kg-install:{harness.value}:end -->"
    block = (f"{begin}\n## Local knowledge graph\nUse kg-extract, kg-query, and kg-dream for project memory. The kg MCP server is project-scoped.\n{end}\n").encode()
    return begin, end, block


def add_marker(plan: InstallPlan, path: Path) -> None:
    validate_target(path, plan.project_root)
    raw = path.read_bytes() if path.exists() else b""
    try: text = raw.decode()
    except UnicodeDecodeError as exc: raise InstallConflict(f"AGENTS.md is not UTF-8: {path}") from exc
    begin, end, block = marker_bytes(plan.harness)
    if text.count(begin) != text.count(end) or text.count(begin) > 1:
        raise InstallConflict(f"Malformed or duplicate kg ownership markers: {path}")
    if begin in text:
        start, finish = text.index(begin), text.index(end)
        if start > finish or begin in text[start + len(begin):finish]:
            raise InstallConflict(f"Malformed or nested kg ownership markers: {path}")
        existing = text[start:finish + len(end)] + ("\n" if text[finish + len(end):].startswith("\n") else "")
        if existing.encode() != block:
            raise InstallConflict(f"Owned marker block has drifted: {path}")
        if not plan.existing_manifest:
            raise InstallConflict(f"Pre-existing unowned marker refused: {path}")
        return
    separator = b"" if not raw or raw.endswith(b"\n") else b"\n"
    plan.writes.append(PlannedWrite(path, raw + separator + block, plan.project_root, ArtifactKind.MARKER_BLOCK, ownership_marker=f"{begin}…{end}"))


def add_owned_file(plan: InstallPlan, path: Path, data: bytes, marker: str) -> None:
    validate_target(path, plan.project_root)
    if path.exists():
        if plan.existing_manifest and path.is_file() and path.read_bytes() == data:
            return
        raise InstallConflict(f"Owned destination already exists: {path}")
    plan.writes.append(PlannedWrite(path, data, plan.project_root, ArtifactKind.OWNED_FILE, ownership_marker=marker))


def finish_plan(plan: InstallPlan) -> InstallPlan:
    validate_target(manifest_path(plan.project_root), plan.project_root)
    if plan.existing_manifest and (plan.writes or plan.skills):
        raise InstallConflict("Existing manifest does not match installation; uninstall first")
    return plan


def _backup(path: Path, backup: Path) -> tuple[str | None, str | None]:
    if not path.exists(): return None, None
    backup.parent.mkdir(parents=True, exist_ok=True)
    data = path.read_bytes(); atomic_write(backup, data)
    return str(backup), content_hash(data)


def _copy_tree(
    source: Path, destination: Path, backup: Path | None = None
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-kg-", dir=destination.parent))
    moved = False
    try:
        shutil.copytree(source, tmp / destination.name)
        staged = tmp / destination.name
        if destination.exists():
            if backup is None:
                raise InstallConflict(f"Backup path required to replace tree: {destination}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, backup)
            moved = True
        try:
            os.replace(staged, destination)
        except Exception:
            if moved:
                if destination.exists():
                    shutil.rmtree(destination)
                os.replace(backup, destination)
            raise
        dir_fd = os.open(destination.parent, os.O_RDONLY)
        try: os.fsync(dir_fd)
        finally: os.close(dir_fd)
    finally: shutil.rmtree(tmp, ignore_errors=True)


def value_hash(value: Any) -> str:
    return content_hash(json.dumps(value, sort_keys=True).encode())


def apply_plan(plan: InstallPlan) -> InstallManifest:
    if plan.existing_manifest and not plan.paths:
        return plan.existing_manifest
    manifest = InstallManifest(project_root=str(plan.project_root), harness=plan.harness)
    backup_root = plan.project_root / ".kg-install-backups" / manifest.transaction_id
    _check_no_symlink_escape(backup_root, plan.project_root)
    originals: list[tuple[Path, bytes | None, int | None, bool]] = []
    artifacts: list[InstalledArtifact] = []
    try:
        for index, skill in enumerate(plan.skills):
            validate_target(skill.destination, skill.root)
            originals.append((skill.destination, None, None, False)); _copy_tree(skill.source, skill.destination, None)
            artifacts.append(InstalledArtifact(str(skill.destination), ArtifactKind.COPIED_SKILL, tree_hash(skill.destination), ownership_marker=f"kg-install:{plan.harness.value}:skill"))
        for index, write in enumerate(plan.writes):
            validate_target(write.path, write.root)
            existed = write.path.exists(); old = write.path.read_bytes() if existed else None
            mode = stat.S_IMODE(write.path.stat().st_mode) if existed else None
            backup_path, backup_sha = _backup(write.path, backup_root / f"file-{index}")
            originals.append((write.path, old, mode, existed)); atomic_write(write.path, write.data)
            if write.kind is ArtifactKind.JSON_OBJECT:
                top = write.fingerprint.split("/")[1] if write.fingerprint else ""
                digest = value_hash(json.loads(write.data)[top]["kg"])
            elif write.kind is ArtifactKind.MARKER_BLOCK:
                digest = content_hash(marker_bytes(plan.harness)[2])
            else: digest = content_hash(write.data)
            artifacts.append(InstalledArtifact(str(write.path), write.kind, digest, backup_path, backup_sha, write.fingerprint, write.prior_value, write.ownership_marker))
        manifest.artifacts = artifacts; manifest.transaction_state = TransactionState.COMMITTED
        save_manifest(manifest, plan.project_root)
        return manifest
    except Exception:
        for path, old, mode, existed in reversed(originals):
            try:
                if path.is_dir(): shutil.rmtree(path)
                elif existed and old is not None:
                    atomic_write(path, old)
                    if mode is not None: os.chmod(path, mode)
                elif path.exists(): path.unlink()
            except OSError: pass
        raise


def uninstall(manifest: InstallManifest, harness: Harness, project_root: Path) -> None:
    if manifest.harness is not harness or manifest.transaction_state is not TransactionState.COMMITTED:
        raise InstallConflict("Manifest harness/state mismatch")
    project = Path(project_root).resolve(strict=True)
    declared = Path(manifest.project_root).resolve(strict=False)
    if declared != project:
        raise InstallConflict(
            f"Manifest project_root {declared} does not match caller's project_root {project}"
        )
    changes: list[tuple[Path, bytes | None]] = []; skills: list[tuple[Path, bytes]] = []
    for artifact in manifest.artifacts:
        path = Path(artifact.path); validate_target(path, project)
        if artifact.kind is ArtifactKind.COPIED_SKILL:
            if not path.is_dir() or tree_hash(path) != artifact.content_sha256: raise InstallConflict(f"Skill drift; refusing uninstall: {path}")
            skills.append((path, _tar(path))); continue
        if not path.is_file() or path.is_symlink(): raise InstallConflict(f"Owned file missing or unsafe: {path}")
        raw = path.read_bytes()
        if artifact.kind is ArtifactKind.JSON_OBJECT:
            data = json_object(path); parts = (artifact.fingerprint or "").strip("/").split("/")
            if len(parts) != 2 or not isinstance(data.get(parts[0]), dict): raise InstallConflict(f"MCP fragment drift; refusing uninstall: {path}")
            current = data[parts[0]].get(parts[1], _MISSING)
            if value_hash(current) != artifact.content_sha256: raise InstallConflict(f"MCP fragment drift; refusing uninstall: {path}")
            merged = dict(data[parts[0]]); del merged[parts[1]]; data[parts[0]] = merged
            changes.append((path, None if artifact.backup_path is None and data == {parts[0]: {}} else dump_json(data)))
        elif artifact.kind is ArtifactKind.MARKER_BLOCK:
            begin, end, block = marker_bytes(harness); text = raw.decode()
            if text.count(begin) != 1 or text.count(end) != 1: raise InstallConflict(f"Marker drift; refusing uninstall: {path}")
            start, finish = text.index(begin), text.index(end) + len(end)
            if text[start:finish].encode() + b"\n" != block: raise InstallConflict(f"Marker drift; refusing uninstall: {path}")
            if finish < len(text) and text[finish] == "\n": finish += 1
            remainder = (text[:start] + text[finish:]).encode()
            changes.append((path, None if artifact.backup_path is None and not remainder else remainder))
        else:
            if content_hash(raw) != artifact.content_sha256: raise InstallConflict(f"Owned file drift; refusing uninstall: {path}")
            changes.append((path, None))
    originals: list[tuple[Path, bytes]] = []
    removed_skills: list[tuple[Path, bytes]] = []
    try:
        for path, data in changes:
            originals.append((path, path.read_bytes()))
            if data is None: path.unlink()
            else: atomic_write(path, data)
        for path, snapshot in skills:
            shutil.rmtree(path)
            removed_skills.append((path, snapshot))
        mpath = manifest_path(project)
        if mpath.exists(): mpath.unlink()
        backups = project / ".kg-install-backups" / manifest.transaction_id
        if backups.exists(): shutil.rmtree(backups)
    except Exception:
        for path, snapshot in reversed(removed_skills):
            try: shutil.rmtree(path, ignore_errors=True)
            except OSError: pass
            _untar(path, snapshot)
        for path, data in reversed(originals): atomic_write(path, data)
        raise


def _tar(root: Path) -> bytes:
    """Snapshot an owned skill tree to bytes for restore-on-failure."""
    buf = bytearray()
    for item in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        rel = item.relative_to(root).as_posix().encode() + b"\0"
        if item.is_dir():
            buf += b"D\0" + rel
        elif item.is_file():
            buf += b"F\0" + rel + item.read_bytes() + b"\0"
    return bytes(buf)


def _untar(root: Path, snapshot: bytes) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    cursor = 0
    n = len(snapshot)
    while cursor < n:
        kind = snapshot[cursor:cursor + 2]
        cursor += 2
        end = snapshot.index(b"\0", cursor)
        rel = snapshot[cursor:end].decode()
        cursor = end + 1
        target = root / rel
        if kind == b"D\0":
            target.mkdir(parents=True, exist_ok=True)
        elif kind == b"F\0":
            fend = snapshot.index(b"\0", cursor)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(snapshot[cursor:fend])
            cursor = fend + 1
        else:
            raise InstallConflict(f"corrupt skill snapshot byte kind: {kind!r}")
