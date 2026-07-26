"""Safe, transactional Claude Code installer.

Planning is read-only. ``apply_plan`` is the sole installation write boundary.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from .manifest import (
    ArtifactKind,
    Harness,
    InstalledArtifact,
    InstallManifest,
    TransactionState,
    atomic_write,
    content_hash,
    load_manifest,
    manifest_path,
    save_manifest,
)

SKILLS = ("kg-extract", "kg-query", "kg-dream")
MCP_NAME = "kg"
MARKER_BEGIN = "<!-- kg-install:claude:begin -->"
MARKER_END = "<!-- kg-install:claude:end -->"
STANZA = (
    f"{MARKER_BEGIN}\n"
    "## Local knowledge graph\n"
    "Use the installed kg-extract, kg-query, and kg-dream skills for project memory.\n"
    f"{MARKER_END}\n"
).encode()
_MISSING = {"__kg_install_missing__": True}


class InstallConflict(RuntimeError):
    """Unsafe pre-existing state prevents an install or uninstall."""


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
    replace: bool = False


@dataclass
class InstallPlan:
    project_root: Path
    home_root: Path
    skills_src: Path
    force: bool = False
    writes: list[PlannedWrite] = field(default_factory=list)
    skills: list[PlannedSkill] = field(default_factory=list)
    existing_manifest: InstallManifest | None = None

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple([w.path for w in self.writes] + [s.destination for s in self.skills])

    @property
    def fragments(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in [
                f"/mcpServers/{MCP_NAME}",
                "/hooks/SessionEnd",
                f"{MARKER_BEGIN}…{MARKER_END}",
            ]
            if any(w.fingerprint == value or w.ownership_marker == value for w in self.writes)
        )


def _contained(path: Path, root: Path) -> Path:
    root = root.resolve(strict=False)
    path = Path(path)
    try:
        path.resolve(strict=False).relative_to(root)
    except (ValueError, OSError) as exc:
        raise InstallConflict(f"Path escapes allowed root {root}: {path}") from exc
    if path.is_symlink():
        raise InstallConflict(f"Symlink target refused: {path}")
    return path


def _validate_target(path: Path, root: Path) -> None:
    _contained(path, root)
    current = path.parent
    while current != root.parent:
        if current.exists() and current.is_symlink():
            _contained(current, root)
        if current == root:
            break
        current = current.parent


def _json(path: Path) -> tuple[dict[str, Any], bytes | None]:
    if not path.exists():
        return {}, None
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InstallConflict(f"Malformed JSON refused: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InstallConflict(f"JSON config must be an object: {path}")
    return value, raw


def _dump(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def _tree_hash(path: Path) -> str:
    digest = sha256()
    for item in sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix()):
        if item.is_symlink():
            raise InstallConflict(f"Symlink in skill refused: {item}")
        rel = item.relative_to(path).as_posix().encode()
        digest.update(b"D\0" if item.is_dir() else b"F\0")
        digest.update(rel + b"\0")
        if item.is_file():
            digest.update(item.read_bytes())
    return digest.hexdigest()


def _same_tree(left: Path, right: Path) -> bool:
    return left.is_dir() and right.is_dir() and _tree_hash(left) == _tree_hash(right)


def _prior(value: Any) -> str:
    return json.dumps(value if value is not None else _MISSING, sort_keys=True)


def _owned_manifest(project: Path) -> InstallManifest | None:
    path = manifest_path(project)
    if not path.exists():
        return None
    manifest = load_manifest(project)
    if manifest.harness is not Harness.CLAUDE:
        raise InstallConflict(f"Manifest belongs to {manifest.harness.value}, not claude")
    if manifest.transaction_state is not TransactionState.COMMITTED:
        raise InstallConflict(
            f"Incomplete install transaction {manifest.transaction_id}; recover before retrying"
        )
    return manifest


def plan_claude_install(
    project_root: Path,
    home_root: Path,
    *,
    skills_src: Path | None = None,
    force: bool = False,
) -> InstallPlan:
    """Build a deterministic, read-only Claude Code installation plan."""
    project = Path(project_root).resolve(strict=True)
    home = Path(home_root).resolve(strict=True)
    source = Path(skills_src or project / "skills").resolve(strict=True)
    if not project.is_dir() or not home.is_dir() or not source.is_dir():
        raise InstallConflict("project_root, home_root, and skills_src must be directories")

    manifest = _owned_manifest(project)
    plan = InstallPlan(project, home, source, force, existing_manifest=manifest)
    claude_root = home / ".claude"
    skill_root = claude_root / "skills" / "kg"
    mcp_path = home / ".claude.json"
    settings_path = claude_root / "settings.json"
    instructions_path = project / "CLAUDE.md"
    for target, root in (
        (skill_root, home),
        (mcp_path, home),
        (settings_path, home),
        (instructions_path, project),
        (manifest_path(project), project),
        (project / ".kg-install-backups", project),
    ):
        _validate_target(target, root)

    for name in SKILLS:
        src = source / name
        if not src.is_dir() or not (src / "SKILL.md").is_file():
            raise InstallConflict(f"Required skill missing: {src}")
        _contained(src, source)
        _tree_hash(src)
        dest = skill_root / name
        _validate_target(dest, home)
        if dest.exists():
            if _same_tree(src, dest):
                continue
            if not force:
                raise InstallConflict(
                    f"Skill destination exists with different content: {dest}; use force to back it up"
                )
            plan.skills.append(PlannedSkill(src, dest, home, replace=True))
        else:
            plan.skills.append(PlannedSkill(src, dest, home))

    mcp, _ = _json(mcp_path)
    servers = mcp.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallConflict(f"mcpServers must be an object: {mcp_path}")
    wanted_mcp = {
        "command": "uv",
        "args": ["run", "kg", "mcp", "serve", "--project-root", str(project)],
    }
    old_mcp = servers.get(MCP_NAME, _MISSING)
    if old_mcp != _MISSING and old_mcp != wanted_mcp:
        raise InstallConflict(f"MCP server name collision at {MCP_NAME!r}: {mcp_path}")
    if old_mcp == _MISSING:
        updated = dict(mcp)
        updated_servers = dict(servers)
        updated_servers[MCP_NAME] = wanted_mcp
        updated["mcpServers"] = updated_servers
        plan.writes.append(
            PlannedWrite(
                mcp_path,
                _dump(updated),
                home,
                ArtifactKind.JSON_OBJECT,
                f"/mcpServers/{MCP_NAME}",
                _prior(_MISSING),
            )
        )

    settings, _ = _json(settings_path)
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallConflict(f"hooks must be an object: {settings_path}")
    session_end = hooks.get("SessionEnd", [])
    if not isinstance(session_end, list):
        raise InstallConflict(f"hooks.SessionEnd must be a list: {settings_path}")
    argv = [
        "uv", "run", "kg", "hook", "session-end", "--project-root", str(project)
    ]
    command = shlex.join(argv)
    wanted_hook = {
        "matcher": "*",
        "hooks": [{"type": "command", "command": command}],
    }
    matches = [i for i, item in enumerate(session_end) if item == wanted_hook]
    if len(matches) > 1:
        raise InstallConflict(f"Duplicate kg SessionEnd hooks already exist: {settings_path}")
    if not matches:
        updated = dict(settings)
        updated_hooks = dict(hooks)
        updated_hooks["SessionEnd"] = [*session_end, wanted_hook]
        updated["hooks"] = updated_hooks
        plan.writes.append(
            PlannedWrite(
                settings_path,
                _dump(updated),
                home,
                ArtifactKind.JSON_LIST_MEMBER,
                "/hooks/SessionEnd",
                json.dumps(wanted_hook, sort_keys=True),
            )
        )

    original = instructions_path.read_bytes() if instructions_path.exists() else b""
    try:
        text = original.decode()
    except UnicodeDecodeError as exc:
        raise InstallConflict(f"CLAUDE.md is not UTF-8: {instructions_path}") from exc
    begins, ends = text.count(MARKER_BEGIN), text.count(MARKER_END)
    if (begins, ends) not in ((0, 0), (1, 1)):
        raise InstallConflict(f"Malformed or duplicate kg ownership markers: {instructions_path}")
    if begins:
        start, end = text.index(MARKER_BEGIN), text.index(MARKER_END)
        if start > end or MARKER_BEGIN in text[start + len(MARKER_BEGIN):end]:
            raise InstallConflict(f"Malformed or nested kg ownership markers: {instructions_path}")
        existing = text[start:end + len(MARKER_END)] + (
            "\n" if text[end + len(MARKER_END):].startswith("\n") else ""
        )
        if existing.encode() != STANZA:
            raise InstallConflict(f"Owned marker block has drifted: {instructions_path}")
    else:
        separator = b"" if not original or original.endswith(b"\n") else b"\n"
        plan.writes.append(
            PlannedWrite(
                instructions_path,
                original + separator + STANZA,
                project,
                ArtifactKind.MARKER_BLOCK,
                ownership_marker=f"{MARKER_BEGIN}…{MARKER_END}",
            )
        )

    if manifest and not plan.writes and not plan.skills:
        return plan
    if manifest:
        raise InstallConflict("Existing install manifest does not match current installation; uninstall first")
    return plan


def _backup(path: Path, backup: Path) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, None
    backup.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        shutil.copytree(path, backup)
        return str(backup), _tree_hash(backup)
    data = path.read_bytes()
    atomic_write(backup, data)
    return str(backup), content_hash(data)


def _copy_skill_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-kg-skill-", dir=destination.parent))
    try:
        shutil.copytree(source, tmp / destination.name)
        staged = tmp / destination.name
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staged, destination)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def apply_plan(plan: InstallPlan) -> InstallManifest:
    """Apply a preflighted plan; rollback every mutation on failure."""
    if plan.existing_manifest and not plan.writes and not plan.skills:
        return plan.existing_manifest
    manifest = InstallManifest(project_root=str(plan.project_root), harness=Harness.CLAUDE)
    backup_root = plan.project_root / ".kg-install-backups" / manifest.transaction_id
    originals: list[tuple[Path, bytes | None, int | None, bool]] = []
    artifacts: list[InstalledArtifact] = []
    try:
        for index, skill in enumerate(plan.skills):
            _validate_target(skill.destination, skill.root)
            existed = skill.destination.exists()
            backup_path, backup_sha = _backup(
                skill.destination, backup_root / f"skill-{index}-{skill.destination.name}"
            )
            originals.append((skill.destination, None, None, existed))
            _copy_skill_atomic(skill.source, skill.destination)
            artifacts.append(
                InstalledArtifact(
                    path=str(skill.destination),
                    kind=ArtifactKind.COPIED_SKILL,
                    content_sha256=_tree_hash(skill.destination),
                    backup_path=backup_path,
                    backup_sha256=backup_sha,
                    ownership_marker="kg-install:claude:skill",
                )
            )

        for index, write in enumerate(plan.writes):
            _validate_target(write.path, write.root)
            existed = write.path.exists()
            old = write.path.read_bytes() if existed else None
            mode = stat.S_IMODE(write.path.stat().st_mode) if existed else None
            backup_path, backup_sha = _backup(write.path, backup_root / f"file-{index}")
            originals.append((write.path, old, mode, existed))
            atomic_write(write.path, write.data)
            if write.kind is ArtifactKind.MARKER_BLOCK:
                value_hash = content_hash(STANZA)
            elif write.kind is ArtifactKind.JSON_OBJECT:
                value_hash = _artifact_value_hash(json.loads(write.data)["mcpServers"][MCP_NAME])
            elif write.kind is ArtifactKind.JSON_LIST_MEMBER:
                value_hash = _artifact_value_hash(json.loads(write.prior_value or "{}"))
            else:
                value_hash = content_hash(write.data)
            artifacts.append(
                InstalledArtifact(
                    path=str(write.path),
                    kind=write.kind,
                    content_sha256=value_hash,
                    backup_path=backup_path,
                    backup_sha256=backup_sha,
                    fingerprint=write.fingerprint,
                    prior_value=write.prior_value,
                    ownership_marker=write.ownership_marker,
                )
            )
        manifest.artifacts = artifacts
        manifest.transaction_state = TransactionState.COMMITTED
        save_manifest(manifest, plan.project_root)  # written last
        return manifest
    except Exception:
        manifest.transaction_state = TransactionState.ROLLBACK_NEEDED
        for path, old, mode, existed in reversed(originals):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                    # Restore directory backup by matching artifact path.
                    artifact = next((a for a in artifacts if a.path == str(path)), None)
                    if artifact and artifact.backup_path:
                        shutil.copytree(artifact.backup_path, path)
                elif existed and old is not None:
                    atomic_write(path, old)
                    if mode is not None:
                        os.chmod(path, mode)
                elif path.exists():
                    path.unlink()
            except OSError:
                pass
        raise


def _artifact_value_hash(value: Any) -> str:
    return content_hash(json.dumps(value, sort_keys=True).encode())


def uninstall(manifest: InstallManifest, *, force: bool = False) -> None:
    """Remove exact owned fragments; refuse drift before any mutation."""
    if manifest.harness is not Harness.CLAUDE:
        raise InstallConflict("Manifest is not a Claude install")
    project = Path(manifest.project_root).resolve(strict=True)
    if manifest.transaction_state is not TransactionState.COMMITTED:
        raise InstallConflict("Cannot uninstall an incomplete transaction")

    changes: list[tuple[Path, bytes | None]] = []
    skill_actions: list[InstalledArtifact] = []
    for artifact in manifest.artifacts:
        path = Path(artifact.path)
        root = project if path == project / "CLAUDE.md" else (
            project if str(path).startswith(str(project) + os.sep) else None
        )
        if root is None:
            # Home root is the ancestor immediately above .claude/.claude.json.
            root = path.parent if path.name == ".claude.json" else next(
                (p.parent for p in path.parents if p.name == ".claude"), path.parent
            )
        _validate_target(path, root)
        if artifact.kind is ArtifactKind.COPIED_SKILL:
            if not path.is_dir() or _tree_hash(path) != artifact.content_sha256:
                raise InstallConflict(f"Skill drift; refusing uninstall: {path}")
            skill_actions.append(artifact)
            continue
        if not path.is_file() or path.is_symlink():
            raise InstallConflict(f"Owned file missing or unsafe: {path}")
        raw = path.read_bytes()
        if artifact.kind in (ArtifactKind.JSON_OBJECT, ArtifactKind.JSON_LIST_MEMBER):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise InstallConflict(f"Malformed owned JSON; refusing uninstall: {path}") from exc
            if artifact.kind is ArtifactKind.JSON_OBJECT:
                current = data.get("mcpServers", {}).get(MCP_NAME, _MISSING)
                if _artifact_value_hash(current) != artifact.content_sha256:
                    raise InstallConflict(f"MCP fragment drift; refusing uninstall: {path}")
                prior = json.loads(artifact.prior_value or json.dumps(_MISSING))
                servers = dict(data["mcpServers"])
                if prior == _MISSING:
                    del servers[MCP_NAME]
                else:
                    servers[MCP_NAME] = prior
                data["mcpServers"] = servers
            else:
                wanted = json.loads(artifact.prior_value or "{}")
                members = data.get("hooks", {}).get("SessionEnd", [])
                matches = [i for i, member in enumerate(members) if member == wanted]
                if len(matches) != 1 or _artifact_value_hash(wanted) != artifact.content_sha256:
                    raise InstallConflict(f"SessionEnd hook drift; refusing uninstall: {path}")
                updated_hooks = dict(data["hooks"])
                updated_hooks["SessionEnd"] = members[:matches[0]] + members[matches[0] + 1:]
                data["hooks"] = updated_hooks
            changes.append((path, _dump(data)))
        elif artifact.kind is ArtifactKind.MARKER_BLOCK:
            text = raw.decode()
            if text.count(MARKER_BEGIN) != 1 or text.count(MARKER_END) != 1:
                raise InstallConflict(f"Marker drift; refusing uninstall: {path}")
            start, end = text.index(MARKER_BEGIN), text.index(MARKER_END) + len(MARKER_END)
            if text[start:end].encode() + b"\n" != STANZA:
                raise InstallConflict(f"Marker block drift; refusing uninstall: {path}")
            if end < len(text) and text[end] == "\n":
                end += 1
            changes.append((path, (text[:start] + text[end:]).encode()))

    originals: list[tuple[Path, bytes]] = []
    try:
        for path, data in changes:
            originals.append((path, path.read_bytes()))
            atomic_write(path, data or b"")
        for artifact in skill_actions:
            path = Path(artifact.path)
            shutil.rmtree(path)
            if artifact.backup_path:
                shutil.copytree(artifact.backup_path, path)
        mpath = manifest_path(project)
        if mpath.exists():
            mpath.unlink()
        backup_root = project / ".kg-install-backups" / manifest.transaction_id
        if backup_root.exists():
            shutil.rmtree(backup_root)
    except Exception:
        for path, data in reversed(originals):
            atomic_write(path, data)
        raise
