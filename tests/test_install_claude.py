import json
import os
import stat
from pathlib import Path

import pytest

from kg.install.claude import (
    MARKER_BEGIN,
    STANZA,
    InstallConflict,
    apply_plan,
    plan_claude_install,
    uninstall,
)
from kg.install.manifest import ArtifactKind, load_manifest


def roots(tmp_path: Path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skills = tmp_path / "skills"
    home.mkdir(); project.mkdir(); skills.mkdir()
    for name in ("kg-extract", "kg-query", "kg-dream"):
        directory = skills / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(name)
    return home, project, skills


def plan(tmp_path: Path, **kwargs):
    home, project, skills = roots(tmp_path)
    return home, project, plan_claude_install(project, home, skills_src=skills, **kwargs)


def test_dry_run_is_deterministic_and_does_not_mutate(tmp_path):
    home, project, first = plan(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    second = plan_claude_install(project, home, skills_src=first.skills_src)
    assert first.paths == second.paths
    assert first.fragments == second.fragments
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before
    assert not (home / ".claude.json").exists()


def test_apply_exact_expected_and_manifest_ownership(tmp_path):
    home, project, planned = plan(tmp_path)
    manifest = apply_plan(planned)
    mcp = json.loads((home / ".claude.json").read_text())
    assert mcp["mcpServers"]["kg"] == {
        "command": "kg",
        "args": ["mcp", "serve", "--project-root", str(project.resolve())],
    }
    hook = json.loads((home / ".claude/settings.json").read_text())["hooks"]["SessionEnd"][0]
    assert hook["matcher"] == "*"
    assert hook["hooks"][0]["type"] == "command"
    assert hook["hooks"][0]["command"] == (
        f"kg hook session-end --project-root {project.resolve()}"
        f" --session-root {project.resolve() / '.kg' / 'sessions'}"
    )
    assert "sh -c" not in hook["hooks"][0]["command"]
    # Trusted session-root dir is created at apply time
    assert (project / ".kg" / "sessions").is_dir()
    assert STANZA == (project / "CLAUDE.md").read_bytes()
    for name in ("kg-extract", "kg-query", "kg-dream"):
        assert (home / ".claude/skills/kg" / name / "SKILL.md").read_text() == name
    loaded = load_manifest(project)
    assert loaded.transaction_state.value == "committed"
    assert {a.kind for a in loaded.artifacts} == {
        ArtifactKind.COPIED_SKILL,
        ArtifactKind.JSON_OBJECT,
        ArtifactKind.JSON_LIST_MEMBER,
        ArtifactKind.MARKER_BLOCK,
    }
    assert all(a.ownership_marker or a.fingerprint for a in loaded.artifacts)
    assert manifest.transaction_id == loaded.transaction_id


def test_reinstall_exact_is_noop(tmp_path):
    home, project, planned = plan(tmp_path)
    manifest = apply_plan(planned)
    paths = [home / ".claude.json", home / ".claude/settings.json", project / "CLAUDE.md", project / ".kg-install-manifest.json"]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    reinstall = plan_claude_install(project, home, skills_src=planned.skills_src)
    assert not reinstall.paths
    assert apply_plan(reinstall).transaction_id == manifest.transaction_id
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before


def test_config_preserved_and_hook_order(tmp_path):
    home, project, skills = roots(tmp_path)
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    existing = {"matcher": "abc", "hooks": [{"type": "command", "command": "first"}]}
    (home / ".claude/settings.json").write_text(json.dumps({"permissions": {"allow": ["Read"]}, "hooks": {"SessionEnd": [existing]}}))
    apply_plan(plan_claude_install(project, home, skills_src=skills))
    mcp = json.loads((home / ".claude.json").read_text())
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert mcp["theme"] == "dark" and mcp["mcpServers"]["other"] == {"command": "x"}
    assert settings["permissions"] == {"allow": ["Read"]}
    assert settings["hooks"]["SessionEnd"][0] == existing


@pytest.mark.parametrize("relative", [Path(".claude.json"), Path(".claude/settings.json")])
def test_malformed_json_refuses_without_write(tmp_path, relative):
    home, project, skills = roots(tmp_path)
    target = home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{broken")
    with pytest.raises(InstallConflict, match="Malformed JSON"):
        plan_claude_install(project, home, skills_src=skills)
    assert target.read_text() == "{broken"


def test_mcp_collision_refuses(tmp_path):
    home, project, skills = roots(tmp_path)
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"kg": {"command": "mine"}}}))
    with pytest.raises(InstallConflict, match="collision"):
        plan_claude_install(project, home, skills_src=skills)


def test_hook_deduplicates_exact_member(tmp_path):
    home, project, skills = roots(tmp_path)
    first = plan_claude_install(project, home, skills_src=skills)
    apply_plan(first)
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert len(settings["hooks"]["SessionEnd"]) == 1


@pytest.mark.parametrize("contents", [
    f"{MARKER_BEGIN}\n", f"{MARKER_BEGIN}\nx\n{MARKER_BEGIN}\ny\n<!-- kg-install:claude:end -->\n",
])
def test_marker_malformed_or_nested_refuses(tmp_path, contents):
    home, project, skills = roots(tmp_path)
    (project / "CLAUDE.md").write_text(contents)
    with pytest.raises(InstallConflict, match="marker"):
        plan_claude_install(project, home, skills_src=skills)


def test_skill_conflict_requires_force_and_backup(tmp_path):
    home, project, skills = roots(tmp_path)
    target = home / ".claude/skills/kg/kg-query"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("user")
    with pytest.raises(InstallConflict, match="use force"):
        plan_claude_install(project, home, skills_src=skills)
    manifest = apply_plan(plan_claude_install(project, home, skills_src=skills, force=True))
    artifact = next(a for a in manifest.artifacts if a.path == str(target))
    assert artifact.backup_path and Path(artifact.backup_path, "SKILL.md").read_text() == "user"


def test_force_skill_replace_failure_restores_user_directory(tmp_path, monkeypatch):
    home, project, skills = roots(tmp_path)
    target = home / ".claude/skills/kg/kg-query"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("user")
    (target / "nested").mkdir()
    (target / "nested/data.bin").write_bytes(b"\x00user\xff")
    before = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    planned = plan_claude_install(project, home, skills_src=skills, force=True)

    import kg.install.claude as module
    real_replace = module.os.replace
    calls = 0

    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("replace failed")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(OSError, match="replace failed"):
        apply_plan(planned)

    after = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_force_skill_post_copy_failure_restores_user_directory(tmp_path, monkeypatch):
    home, project, skills = roots(tmp_path)
    target = home / ".claude/skills/kg/kg-query"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("user")
    planned = plan_claude_install(project, home, skills_src=skills, force=True)

    import kg.install.claude as module
    real_hash = module._tree_hash

    def fail_installed_hash(path):
        if path == target and (target / "SKILL.md").read_text() != "user":
            raise OSError("post-copy failed")
        return real_hash(path)

    monkeypatch.setattr(module, "_tree_hash", fail_installed_hash)
    with pytest.raises(OSError, match="post-copy failed"):
        apply_plan(planned)

    assert (target / "SKILL.md").read_text() == "user"


def test_symlink_escape_refuses(tmp_path):
    home, project, skills = roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".claude").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InstallConflict, match="escapes allowed root"):
        plan_claude_install(project, home, skills_src=skills)


def test_backup_path_symlink_escape_refused_at_apply(tmp_path):
    home, project, planned = plan(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".kg-install-backups").symlink_to(outside, target_is_directory=True)

    with pytest.raises((InstallConflict, ValueError), match="Symlink|escapes"):
        apply_plan(planned)
    assert not list(outside.iterdir())


def test_atomic_failure_rolls_back(tmp_path, monkeypatch):
    home, project, planned = plan(tmp_path)
    import kg.install.claude as module
    real = module.atomic_write
    calls = 0
    def fail_second(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        real(path, data)
    monkeypatch.setattr(module, "atomic_write", fail_second)
    with pytest.raises(OSError, match="injected"):
        apply_plan(planned)
    assert not (home / ".claude.json").exists()
    assert not (project / ".kg-install-manifest.json").exists()
    assert not (home / ".claude/skills/kg/kg-extract").exists()


def test_uninstall_restores_exact_and_preserves_user_additions(tmp_path):
    home, project, skills = roots(tmp_path)
    (home / ".claude").mkdir()
    original_mcp = {"user": 1, "mcpServers": {"other": {"command": "x"}}}
    original_settings = {"hooks": {"SessionEnd": [{"matcher": "old", "hooks": []}]}}
    original_claude = b"# User instructions\n"
    (home / ".claude.json").write_text(json.dumps(original_mcp))
    (home / ".claude/settings.json").write_text(json.dumps(original_settings))
    (project / "CLAUDE.md").write_bytes(original_claude)
    manifest = apply_plan(plan_claude_install(project, home, skills_src=skills))
    mcp = json.loads((home / ".claude.json").read_text()); mcp["after"] = True
    (home / ".claude.json").write_text(json.dumps(mcp))
    settings = json.loads((home / ".claude/settings.json").read_text()); settings["after"] = True
    (home / ".claude/settings.json").write_text(json.dumps(settings))
    (project / "CLAUDE.md").write_text((project / "CLAUDE.md").read_text() + "after\n")
    uninstall(manifest, project)
    got_mcp = json.loads((home / ".claude.json").read_text())
    got_settings = json.loads((home / ".claude/settings.json").read_text())
    assert got_mcp == {**original_mcp, "after": True}
    assert got_settings == {**original_settings, "after": True}
    assert (project / "CLAUDE.md").read_bytes() == original_claude + b"after\n"
    assert not (project / ".kg-install-manifest.json").exists()


def test_drift_refusal_preserves_everything(tmp_path):
    home, project, planned = plan(tmp_path)
    manifest = apply_plan(planned)
    skill = home / ".claude/skills/kg/kg-query/SKILL.md"
    skill.write_text("edited")
    mcp_before = (home / ".claude.json").read_bytes()
    with pytest.raises(InstallConflict, match="drift"):
        uninstall(manifest, project)
    assert skill.read_text() == "edited"
    assert (home / ".claude.json").read_bytes() == mcp_before


def test_modes_preserved(tmp_path):
    home, project, skills = roots(tmp_path)
    (home / ".claude").mkdir()
    mcp = home / ".claude.json"; mcp.write_text("{}"); mcp.chmod(0o600)
    settings = home / ".claude/settings.json"; settings.write_text("{}"); settings.chmod(0o640)
    instructions = project / "CLAUDE.md"; instructions.write_text("user\n"); instructions.chmod(0o664)
    manifest = apply_plan(plan_claude_install(project, home, skills_src=skills))
    assert stat.S_IMODE(mcp.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.stat().st_mode) == 0o640
    assert stat.S_IMODE(instructions.stat().st_mode) == 0o664
    uninstall(manifest, project)
    assert stat.S_IMODE(mcp.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.stat().st_mode) == 0o640
    assert stat.S_IMODE(instructions.stat().st_mode) == 0o664
