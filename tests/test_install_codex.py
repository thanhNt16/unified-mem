"""Tests for the Codex installer: TOML safety, skills, markers, uninstall."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from kg.install import common
from kg.install.codex import (
    CODEX_CONFIG_REL,
    CODEX_AGENTS_REL,
    InstallConflict,
    plan_codex_install,
    uninstall,
)
from kg.install.common import content_hash
from kg.install.manifest import Harness, load_manifest


def roots(tmp_path: Path):
    home = tmp_path / "home"; project = tmp_path / "project"; skills = tmp_path / "skills"
    home.mkdir(); project.mkdir(); skills.mkdir()
    for name in common.SKILLS:
        d = skills / name; d.mkdir(); (d / "SKILL.md").write_text(f"# {name}\nname: {name}\ndescription: x\n")
    return home, project, skills


def plan(tmp_path: Path, **kw):
    home, project, skills = roots(tmp_path)
    return home, project, plan_codex_install(project, home, skills_src=skills, **kw), skills


def test_dry_run_no_mutation(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert planned.paths
    assert planned.manual_steps == []
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == snapshot


def test_apply_writes_toml_skills_agents(tmp_path):
    home, project, planned, skills = plan(tmp_path)
    common.apply_plan(planned)
    config = project / CODEX_CONFIG_REL
    data = tomllib.loads(config.read_text())
    assert data["mcp_servers"]["kg"]["command"] == "kg"
    assert data["mcp_servers"]["kg"]["args"] == [
        "mcp", "serve", "--project-root", str(project)
    ]
    assert (project / ".agents/skills/kg-extract/SKILL.md").read_text().startswith("# kg-extract")
    text = (project / CODEX_AGENTS_REL).read_text()
    assert "kg-install:codex:begin" in text and "kg-install:codex:end" in text
    loaded = load_manifest(project)
    assert loaded.harness is Harness.CODEX
    assert loaded.transaction_state.value == "committed"


def test_project_path_with_quote_generates_parseable_toml(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / 'project"quoted'
    skills = tmp_path / "skills"
    home.mkdir()
    project.mkdir()
    skills.mkdir()
    for name in common.SKILLS:
        directory = skills / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(name)

    planned = plan_codex_install(project, home, skills_src=skills)
    config = next(write for write in planned.writes if write.path == project / CODEX_CONFIG_REL)
    parsed = tomllib.loads(config.data.decode())
    assert parsed["mcp_servers"]["kg"] == {
        "command": "kg",
        "args": ["mcp", "serve", "--project-root", str(project.resolve())],
    }


def test_reinstall_noop(tmp_path):
    home, project, planned, skills = plan(tmp_path)
    common.apply_plan(planned)
    before = [(p, p.read_bytes(), p.stat().st_mtime_ns)
              for p in (project / CODEX_CONFIG_REL, project / CODEX_AGENTS_REL,
                        project / ".kg-install-manifest.json")]
    reinstall = plan_codex_install(project, home, skills_src=skills)
    assert reinstall.paths == ()
    common.apply_plan(reinstall)
    after = [(p, p.read_bytes(), p.stat().st_mtime_ns)
             for p in (project / CODEX_CONFIG_REL, project / CODEX_AGENTS_REL,
                       project / ".kg-install-manifest.json")]
    assert before == after


def test_uninstall_reverses(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    manifest = common.apply_plan(planned)
    uninstall(manifest, project)
    assert not (project / CODEX_CONFIG_REL).exists()
    assert not (project / ".agents/skills/kg-extract").exists()
    assert not (project / CODEX_AGENTS_REL).exists()
    assert not (project / ".kg-install-manifest.json").exists()


def test_uninstall_rejects_foreign_manifest_root_without_touching_file(tmp_path):
    caller = tmp_path / "caller"
    victim = tmp_path / "victim"
    caller.mkdir()
    victim.mkdir()
    target = victim / "owned.txt"
    target.write_text("keep")
    manifest = common.InstallManifest(
        project_root=str(victim),
        harness=Harness.CODEX,
        transaction_state=common.TransactionState.COMMITTED,
        artifacts=[common.InstalledArtifact(
            path=str(target),
            kind=common.ArtifactKind.OWNED_FILE,
            content_sha256=common.content_hash(target.read_bytes()),
        )],
    )

    with pytest.raises(InstallConflict, match="project_root"):
        uninstall(manifest, caller)
    assert target.read_text() == "keep"


def test_uninstall_wholly_owned_toml_drift_refused(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    manifest = common.apply_plan(planned)
    config = project / CODEX_CONFIG_REL
    config.write_text(config.read_text() + '\n[mcp_servers.other]\ncommand = "x"\n')
    with pytest.raises(InstallConflict, match="drift"):
        uninstall(manifest, project)
    assert tomllib.loads(config.read_text())["mcp_servers"]["other"] == {"command": "x"}


def test_drift_refusal(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    manifest = common.apply_plan(planned)
    (project / ".agents/skills/kg-query/SKILL.md").write_text("edited")
    with pytest.raises(InstallConflict, match="drift"):
        uninstall(manifest, project)
    assert (project / ".agents/skills/kg-query/SKILL.md").read_text() == "edited"


def test_existing_toml_refused_with_manual_plan(tmp_path):
    home, project, skills = roots(tmp_path)
    config_dir = project / ".codex"; config_dir.mkdir()
    (config_dir / "config.toml").write_text('# user\n[mcp_servers.other]\ncommand = "x"\n')
    planned = plan_codex_install(project, home, skills_src=skills)
    assert not any(w.path == project / CODEX_CONFIG_REL for w in planned.writes)
    assert planned.manual_steps and "opencode" not in planned.manual_steps[0].reason
    assert "TOML" in planned.manual_steps[0].reason
    common.apply_plan(planned)
    assert (config_dir / "config.toml").read_text() == '# user\n[mcp_servers.other]\ncommand = "x"\n'
    assert (project / ".agents/skills/kg-extract/SKILL.md").exists()


def test_existing_toml_kg_collision_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    config_dir = project / ".codex"; config_dir.mkdir()
    (config_dir / "config.toml").write_text('[mcp_servers.kg]\ncommand = "other"\n')
    with pytest.raises(InstallConflict, match="collision"):
        plan_codex_install(project, home, skills_src=skills)
    assert (config_dir / "config.toml").read_text() == '[mcp_servers.kg]\ncommand = "other"\n'


def test_malformed_toml_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    config_dir = project / ".codex"; config_dir.mkdir()
    (config_dir / "config.toml").write_text("not = = toml")
    with pytest.raises(InstallConflict, match="Malformed"):
        plan_codex_install(project, home, skills_src=skills)
    assert (config_dir / "config.toml").read_text() == "not = = toml"


def test_common_copy_tree_replace_failure_restores_destination(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    backup = tmp_path / "backup"
    source.mkdir()
    destination.mkdir()
    (source / "SKILL.md").write_text("new")
    (destination / "SKILL.md").write_text("user")

    real_replace = common.os.replace
    calls = 0

    def fail_second(source_path, destination_path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("replace failed")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(common.os, "replace", fail_second)
    with pytest.raises(OSError, match="replace failed"):
        common._copy_tree(source, destination, backup)

    assert (destination / "SKILL.md").read_text() == "user"


def test_symlink_config_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    outside = tmp_path / "outside.toml"; outside.write_text("ok")
    (project / ".codex").mkdir()
    (project / ".codex" / "config.toml").symlink_to(outside)
    with pytest.raises(InstallConflict, match="escapes allowed root|Refusing unsafe"):
        plan_codex_install(project, home, skills_src=skills)
