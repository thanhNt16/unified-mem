import json
import os
import stat
from pathlib import Path

import pytest

from kg.install.manifest import (
    MANIFEST_VERSION,
    ArtifactKind,
    DriftConflict,
    Harness,
    InstalledArtifact,
    InstallManifest,
    TransactionState,
    atomic_write,
    check_drift,
    content_hash,
    load_manifest,
    save_manifest,
)


def _artifact(path: Path, **kwargs) -> InstalledArtifact:
    kwargs.setdefault("kind", ArtifactKind.OWNED_FILE)
    return InstalledArtifact(
        path=str(path),
        content_sha256=content_hash(path.read_bytes()),
        **kwargs,
    )


def _manifest(root: Path, **kwargs) -> InstallManifest:
    return InstallManifest(project_root=str(root), harness=Harness.CLAUDE, **kwargs)


def test_invalid_harness_rejected():
    with pytest.raises(ValueError):
        Harness("claude-code")


def test_roundtrip_all_fields(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text('{"mcp": {"kg": true}}')
    artifact = _artifact(
        target,
        kind=ArtifactKind.JSON_OBJECT,
        backup_path=str(tmp_path / "settings.json.bak"),
        backup_sha256="b" * 64,
        fingerprint="/mcp/kg",
        prior_value="false",
        ownership_marker="kg-install:v1",
    )
    written = _manifest(
        tmp_path,
        artifacts=[artifact],
        transaction_state=TransactionState.COMMITTED,
    )
    save_manifest(written, tmp_path)
    got = load_manifest(tmp_path)
    assert got.version == MANIFEST_VERSION
    assert got.harness is Harness.CLAUDE
    assert got.transaction_state is TransactionState.COMMITTED
    assert got.artifacts == [artifact]
    assert got.transaction_id == written.transaction_id


@pytest.mark.parametrize("state", TransactionState)
def test_transaction_states_roundtrip(tmp_path, state):
    manifest = _manifest(tmp_path, transaction_state=state)
    save_manifest(manifest, tmp_path)
    assert load_manifest(tmp_path).transaction_state is state


def test_corrupt_manifest_fails_closed_without_overwrite(tmp_path):
    path = tmp_path / ".kg-install-manifest.json"
    original = b"{broken json"
    path.write_bytes(original)
    with pytest.raises(ValueError, match="Corrupt manifest"):
        load_manifest(tmp_path)
    assert path.read_bytes() == original


def test_foreign_project_root_rejected_unchanged(tmp_path):
    path = tmp_path / ".kg-install-manifest.json"
    data = _manifest(tmp_path.parent / "victim").to_dict()
    original = json.dumps(data).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match="project_root"):
        load_manifest(tmp_path)
    assert path.read_bytes() == original


def test_unknown_schema_version_rejected_unchanged(tmp_path):
    path = tmp_path / ".kg-install-manifest.json"
    data = _manifest(tmp_path).to_dict()
    data["version"] = MANIFEST_VERSION + 1
    original = json.dumps(data).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match="Unsupported manifest version"):
        load_manifest(tmp_path)
    assert path.read_bytes() == original


def test_missing_required_schema_field_rejected(tmp_path):
    path = tmp_path / ".kg-install-manifest.json"
    data = _manifest(tmp_path).to_dict()
    del data["transaction_id"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="transaction_id"):
        load_manifest(tmp_path)


def test_atomic_write_preserves_existing_mode(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("old")
    path.chmod(0o600)
    atomic_write(path, b"new")
    assert path.read_bytes() == b"new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_atomic_replace_failure_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("old")

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write(path, b"new")
    assert path.read_text() == "old"
    assert not list(tmp_path.glob(".tmp-*"))


def test_symlink_escape_rejected_for_artifact(tmp_path):
    outside = tmp_path.parent / "outside-config.json"
    outside.write_text("outside")
    link = tmp_path / "escape"
    link.symlink_to(outside.parent, target_is_directory=True)
    artifact = InstalledArtifact(
        path=str(link / outside.name),
        kind=ArtifactKind.OWNED_FILE,
        content_sha256=content_hash(outside.read_bytes()),
    )
    with pytest.raises(ValueError, match="escapes containment root"):
        check_drift(artifact, tmp_path)


def test_path_traversal_rejected_for_artifact(tmp_path):
    outside = tmp_path.parent / "outside-config.json"
    outside.write_text("outside")
    artifact = InstalledArtifact(
        path=str(tmp_path / ".." / outside.name),
        kind=ArtifactKind.OWNED_FILE,
        content_sha256=content_hash(outside.read_bytes()),
    )
    with pytest.raises(ValueError, match="escapes containment root"):
        check_drift(artifact, tmp_path)


def test_manifest_path_runs_component_symlink_check(tmp_path, monkeypatch):
    import kg.install.manifest as module

    calls = []
    real_check = module._check_no_symlink_escape

    def recording_check(path, root):
        calls.append((path, root))
        return real_check(path, root)

    monkeypatch.setattr(module, "_check_no_symlink_escape", recording_check)
    module.manifest_path(tmp_path)
    assert calls == [(tmp_path / ".kg-install-manifest.json", tmp_path.resolve())]


def test_manifest_symlink_rejected(tmp_path):
    external = tmp_path.parent / "external-manifest.json"
    external.write_text("{}")
    (tmp_path / ".kg-install-manifest.json").symlink_to(external)
    with pytest.raises(ValueError, match="escapes containment root"):
        load_manifest(tmp_path)


def test_matching_owned_artifact_passes_drift_check(tmp_path):
    target = tmp_path / "owned.txt"
    target.write_text("owned")
    check_drift(_artifact(target), tmp_path)


def test_changed_owned_artifact_refuses_uninstall(tmp_path):
    target = tmp_path / "owned.txt"
    target.write_text("owned")
    artifact = _artifact(target)
    target.write_text("user edit")
    with pytest.raises(DriftConflict, match="Refusing uninstall"):
        check_drift(artifact, tmp_path)


def test_missing_owned_artifact_refuses_uninstall(tmp_path):
    target = tmp_path / "owned.txt"
    target.write_text("owned")
    artifact = _artifact(target)
    target.unlink()
    with pytest.raises(DriftConflict, match="missing"):
        check_drift(artifact, tmp_path)


def test_json_fingerprint_requires_structural_match(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text('{"mcp": {"kg": true}}')
    artifact = _artifact(target, fingerprint="/mcp/kg")
    check_drift(artifact, tmp_path)
    # Same digest guard catches content changes before pointer resolution.
    target.write_text('{"mcp": {}}')
    with pytest.raises(DriftConflict):
        check_drift(artifact, tmp_path)


def test_backup_metadata_roundtrips(tmp_path):
    target = tmp_path / "owned.txt"
    backup = tmp_path / "owned.txt.bak"
    target.write_text("new")
    backup.write_text("old")
    manifest = _manifest(
        tmp_path,
        artifacts=[
            _artifact(
                target,
                backup_path=str(backup),
                backup_sha256=content_hash(backup.read_bytes()),
            )
        ],
    )
    save_manifest(manifest, tmp_path)
    got = load_manifest(tmp_path).artifacts[0]
    assert got.backup_path == str(backup)
    assert got.backup_sha256 == content_hash(b"old")


def test_idempotent_find_returns_same_artifact(tmp_path):
    target = tmp_path / "owned.txt"
    target.write_text("owned")
    artifact = _artifact(target)
    manifest = _manifest(tmp_path, artifacts=[artifact])
    assert manifest.find(str(target)) is artifact
    assert manifest.find(str(tmp_path / "other")) is None
