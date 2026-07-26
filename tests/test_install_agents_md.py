"""AGENTS.md harness-neutral installer: marker safety and uninstall."""
from __future__ import annotations

from pathlib import Path

import pytest

from kg.install import common
from kg.install.agents_md import InstallConflict, plan_agents_install, uninstall
from kg.install.manifest import Harness, load_manifest


def roots(tmp_path: Path):
    home = tmp_path / "home"; project = tmp_path / "project"
    home.mkdir(); project.mkdir()
    return home, project


def plan(tmp_path: Path, **kw):
    home, project = roots(tmp_path)
    return home, project, plan_agents_install(project, home, **kw)


def test_apply_writes_marker_only(tmp_path):
    home, project, planned = plan(tmp_path)
    common.apply_plan(planned)
    text = (project / "AGENTS.md").read_text()
    assert "kg-install:agents:begin" in text
    assert "kg-install:agents:end" in text
    assert load_manifest(project).harness is Harness.AGENTS


def test_existing_agents_preserved(tmp_path):
    home, project = roots(tmp_path)
    (project / "AGENTS.md").write_text("# User docs\n\nImportant.\n")
    planned = plan_agents_install(project, home)
    common.apply_plan(planned)
    text = (project / "AGENTS.md").read_text()
    assert text.startswith("# User docs\n\nImportant.\n")
    assert "kg-install:agents:begin" in text


def test_uninstall_strips_only_marker(tmp_path):
    home, project = roots(tmp_path)
    (project / "AGENTS.md").write_text("# User\n")
    manifest = common.apply_plan(plan_agents_install(project, home))
    (project / "AGENTS.md").write_text((project / "AGENTS.md").read_text() + "more\n")
    uninstall(manifest, project)
    assert (project / "AGENTS.md").read_text() == "# User\nmore\n"


def test_malformed_marker_refused(tmp_path):
    home, project = roots(tmp_path)
    begin = "<!-- kg-install:agents:begin -->"
    (project / "AGENTS.md").write_text(begin + "\n")  # begin without end
    with pytest.raises(InstallConflict, match="marker"):
        plan_agents_install(project, home)
    assert (project / "AGENTS.md").read_text() == begin + "\n"


def test_nested_marker_refused(tmp_path):
    home, project = roots(tmp_path)
    begin, end = "<!-- kg-install:agents:begin -->", "<!-- kg-install:agents:end -->"
    (project / "AGENTS.md").write_text(f"{begin}\n{begin}\nx\n{end}\n{end}\n")
    with pytest.raises(InstallConflict, match="marker"):
        plan_agents_install(project, home)


def test_drifted_marker_block_refused(tmp_path):
    home, project = roots(tmp_path)
    (project / "AGENTS.md").write_text("# user\n")
    manifest = common.apply_plan(plan_agents_install(project, home))
    begin, end = "<!-- kg-install:agents:begin -->", "<!-- kg-install:agents:end -->"
    (project / "AGENTS.md").write_text(f"{begin}\ntampered\n{end}\n")
    with pytest.raises(InstallConflict, match="drift"):
        uninstall(manifest, project)


def test_symlink_agents_refused(tmp_path):
    home, project = roots(tmp_path)
    outside = tmp_path / "outside.md"; outside.write_text("x")
    (project / "AGENTS.md").symlink_to(outside)
    with pytest.raises(InstallConflict, match="Refusing unsafe|escapes"):
        plan_agents_install(project, home)
