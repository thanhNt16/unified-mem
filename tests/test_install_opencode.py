"""OpenCode installer: JSON merge, JSONC refusal, skills, markers, uninstall."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kg.install import common
from kg.install.opencode import CONFIG_REL, JSONC_REL, SKILLS_REL, InstallConflict, plan_opencode_install, uninstall
from kg.install.manifest import Harness, load_manifest


def roots(tmp_path: Path):
    home = tmp_path / "home"; project = tmp_path / "project"; skills = tmp_path / "skills"
    home.mkdir(); project.mkdir(); skills.mkdir()
    for name in common.SKILLS:
        d = skills / name; d.mkdir(); (d / "SKILL.md").write_text(f"name: {name}\ndescription: x\n")
    return home, project, skills


def plan(tmp_path: Path, **kw):
    home, project, skills = roots(tmp_path)
    return home, project, plan_opencode_install(project, home, skills_src=skills, **kw), skills


def test_apply_plain_json(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    common.apply_plan(planned)
    cfg = json.loads((project / CONFIG_REL).read_text())
    assert cfg["mcp"]["kg"] == {"type": "local", "command": common.mcp_argv(project), "enabled": True}
    assert (project / SKILLS_REL / "kg-extract" / "SKILL.md").exists()
    assert "kg-install:opencode:begin" in (project / "AGENTS.md").read_text()
    assert load_manifest(project).harness is Harness.OPENCODE


def test_existing_json_preserved_and_merged(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / CONFIG_REL).write_text(json.dumps({"$schema": "x", "mcp": {"other": {"type": "local", "command": ["x"]}}}))
    planned = plan_opencode_install(project, home, skills_src=skills)
    common.apply_plan(planned)
    cfg = json.loads((project / CONFIG_REL).read_text())
    assert cfg["$schema"] == "x"
    assert cfg["mcp"]["other"]["command"] == ["x"]
    assert cfg["mcp"]["kg"]["type"] == "local"


def test_jsonc_refuses_with_manual_plan(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / JSONC_REL).write_text('{\n  // my comment\n  "mcp": {}\n}\n')
    planned = plan_opencode_install(project, home, skills_src=skills)
    assert not any(w.path == project / CONFIG_REL for w in planned.writes)
    assert planned.manual_steps and "JSONC" in planned.manual_steps[0].reason
    common.apply_plan(planned)
    assert (project / JSONC_REL).read_text() == '{\n  // my comment\n  "mcp": {}\n}\n'


def test_jsonc_and_json_both_present_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / CONFIG_REL).write_text("{}")
    (project / JSONC_REL).write_text("{}")
    with pytest.raises(InstallConflict, match="precedence ambiguous"):
        plan_opencode_install(project, home, skills_src=skills)


def test_mcp_collision_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / CONFIG_REL).write_text(json.dumps({"mcp": {"kg": {"type": "local", "command": ["x"]}}}))
    with pytest.raises(InstallConflict, match="collision"):
        plan_opencode_install(project, home, skills_src=skills)


def test_malformed_json_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / CONFIG_REL).write_text("{broken")
    with pytest.raises(InstallConflict, match="Malformed JSON"):
        plan_opencode_install(project, home, skills_src=skills)
    assert (project / CONFIG_REL).read_text() == "{broken"


def test_uninstall_reverses(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    manifest = common.apply_plan(planned)
    uninstall(manifest, project)
    assert not (project / CONFIG_REL).exists()
    assert not (project / "AGENTS.md").exists()
    assert not (project / ".kg-install-manifest.json").exists()


def test_reinstall_noop(tmp_path):
    home, project, planned, skills = plan(tmp_path)
    common.apply_plan(planned)
    before = (project / CONFIG_REL).read_bytes()
    reinstall = plan_opencode_install(project, home, skills_src=skills)
    assert reinstall.paths == ()
    assert (project / CONFIG_REL).read_bytes() == before
