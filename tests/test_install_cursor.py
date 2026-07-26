"""Cursor installer: mcp.json merge, rule file, AGENTS.md marker, uninstall."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kg.install import common
from kg.install.cursor import MCP_REL, RULE, RULE_REL, InstallConflict, plan_cursor_install, uninstall
from kg.install.manifest import Harness, load_manifest


def roots(tmp_path: Path):
    home = tmp_path / "home"; project = tmp_path / "project"; skills = tmp_path / "skills"
    home.mkdir(); project.mkdir(); skills.mkdir()
    return home, project, skills


def plan(tmp_path: Path, **kw):
    home, project, skills = roots(tmp_path)
    return home, project, plan_cursor_install(project, home, skills_src=skills, **kw), skills


def test_apply_mcp_rule_agents(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    common.apply_plan(planned)
    cfg = json.loads((project / MCP_REL).read_text())
    argv = common.mcp_argv(project)
    assert cfg["mcpServers"]["kg"] == {"type": "stdio", "command": argv[0], "args": argv[1:]}
    assert (project / RULE_REL).read_bytes() == RULE
    assert "kg-install:cursor:begin" in (project / "AGENTS.md").read_text()
    assert load_manifest(project).harness is Harness.CURSOR


def test_mcp_collision_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / ".cursor").mkdir()
    (project / MCP_REL).write_text(json.dumps({"mcpServers": {"kg": {"type": "stdio", "command": "x"}}}))
    with pytest.raises(InstallConflict, match="collision"):
        plan_cursor_install(project, home, skills_src=skills)


def test_existing_servers_preserved(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / ".cursor").mkdir()
    (project / MCP_REL).write_text(json.dumps({"mcpServers": {"other": {"type": "stdio", "command": "x"}}, "version": 2}))
    planned = plan_cursor_install(project, home, skills_src=skills)
    common.apply_plan(planned)
    cfg = json.loads((project / MCP_REL).read_text())
    assert cfg["mcpServers"]["other"]["command"] == "x"
    assert cfg["version"] == 2
    assert "kg" in cfg["mcpServers"]


def test_malformed_mcp_json_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / ".cursor").mkdir()
    (project / MCP_REL).write_text("{broken")
    with pytest.raises(InstallConflict, match="Malformed JSON"):
        plan_cursor_install(project, home, skills_src=skills)
    assert (project / MCP_REL).read_text() == "{broken"


def test_rule_collision_refused(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / ".cursor" / "rules").mkdir(parents=True)
    (project / RULE_REL).write_text("user")
    with pytest.raises(InstallConflict, match="already exists"):
        plan_cursor_install(project, home, skills_src=skills)


def test_uninstall_reverses(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    manifest = common.apply_plan(planned)
    uninstall(manifest, project)
    assert not (project / MCP_REL).exists()
    assert not (project / RULE_REL).exists()
    assert not (project / "AGENTS.md").exists()
    assert not (project / ".kg-install-manifest.json").exists()


def test_drift_refusal(tmp_path):
    home, project, planned, _ = plan(tmp_path)
    manifest = common.apply_plan(planned)
    (project / RULE_REL).write_text("edited")
    with pytest.raises(InstallConflict, match="drift"):
        uninstall(manifest, project)
    assert (project / RULE_REL).read_text() == "edited"
