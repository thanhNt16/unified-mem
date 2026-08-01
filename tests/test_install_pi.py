"""Pi installer contract tests."""
from pathlib import Path

from kg.install import common
from kg.install.manifest import Harness, load_manifest
from kg.install.pi import plan_pi_install, uninstall


def roots(tmp_path: Path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skills = tmp_path / "skills"
    home.mkdir()
    project.mkdir()
    skills.mkdir()
    for name in common.SKILLS:
        skill = skills / name
        skill.mkdir()
        (skill / "SKILL.md").write_text(f"name: {name}\ndescription: x\n")
    return home, project, skills


def test_plan_pi_owns_extension_skills_and_context(tmp_path):
    home, project, skills = roots(tmp_path)
    planned = plan_pi_install(project, home, skills_src=skills)
    assert planned.harness is Harness.PI
    assert {skill.destination.relative_to(project).as_posix() for skill in planned.skills} == {
        ".pi/skills/kg-ingest",
        ".pi/skills/kg-extract",
        ".pi/skills/kg-query",
        ".pi/skills/kg-dream",
    }
    assert {write.path.relative_to(project).as_posix() for write in planned.writes} == {
        ".pi/extensions/kg.ts",
        "AGENTS.md",
    }


def test_apply_pi_records_manifest_and_uninstall_preserves_agents(tmp_path):
    home, project, skills = roots(tmp_path)
    (project / "AGENTS.md").write_text("# User rules\n")
    manifest = common.apply_plan(plan_pi_install(project, home, skills_src=skills))
    assert load_manifest(project).harness is Harness.PI
    assert (project / ".pi/extensions/kg.ts").is_file()
    uninstall(manifest, project)
    assert (project / "AGENTS.md").read_text() == "# User rules\n"
    assert not (project / ".pi").exists()
