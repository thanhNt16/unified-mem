"""H1: uninstall skill rmtree must be reversible if a later step throws.

Regression: if the manifest-unlink or backup-cleanup step fails AFTER skill
directories have been rmtree'd, those skill directories must be restored from
the installer's backup so the user is not left in a broken half-uninstalled
state with no recovery path.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from kg.install import common
from kg.install.codex import plan_codex_install, uninstall


def _roots(tmp_path: Path):
    home = tmp_path / "home"; project = tmp_path / "project"; skills = tmp_path / "skills"
    home.mkdir(); project.mkdir(); skills.mkdir()
    for name in common.SKILLS:
        d = skills / name; d.mkdir(); (d / "SKILL.md").write_text(f"# {name}\n")
    return home, project, skills


def test_uninstall_failure_after_skill_rmtree_restores_skills(tmp_path, monkeypatch):
    home, project, skills = _roots(tmp_path)
    planned = plan_codex_install(project, home, skills_src=skills)
    manifest = common.apply_plan(planned)

    # Force a failure on the manifest unlink — it runs AFTER skill rmtree.
    real_unlink = common.Path.unlink
    calls = {"count": 0}

    def fail_manifest_unlink(self, *args, **kwargs):
        # Fail ONLY when removing the manifest file — that step runs AFTER
        # all skill directories have been rmtree'd.
        if self.name == ".kg-install-manifest.json":
            raise OSError("injected failure mid-uninstall")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(common.Path, "unlink", fail_manifest_unlink)
    with pytest.raises(OSError, match="injected failure"):
        uninstall(manifest, project)

    # Skill directories must exist again (restored from backup) even though
    # they were rmtree'd before the failure.
    assert (project / ".agents" / "skills" / "kg-extract" / "SKILL.md").read_text() == "# kg-extract\n"
    assert (project / ".agents" / "skills" / "kg-query" / "SKILL.md").exists()
    assert (project / ".agents" / "skills" / "kg-dream" / "SKILL.md").exists()
