"""kg install CLI: dry-run/apply/uninstall/dispatch + all, error paths."""
from __future__ import annotations

import io
import json
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr

import pytest
from typer.testing import CliRunner

from kg.cli.main import app
from kg.install import common
from kg.install.manifest import Harness

runner = CliRunner()


def _seed_skills(parent: Path) -> Path:
    src = parent / "skills"
    src.mkdir()
    for name in common.SKILLS:
        d = src / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"# {name}\n")
    return src


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / ".kg").mkdir()
    skills = _seed_skills(tmp_path)
    return home, project, skills


# --- dispatch: each harness dry-runs without writing -----------------------


@pytest.mark.parametrize("name", ["claude", "codex", "opencode", "cursor", "agents"])
def test_dry_run_no_writes(tmp_path, name):
    home, project, skills = _roots(tmp_path)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = runner.invoke(
        app,
        [
            "install", name,
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
        ],
    )
    assert result.exit_code == 0, result.output
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == snapshot
    assert f"# plan: {name}" in result.output


def test_all_dry_run_lists_all_harnesses(tmp_path):
    home, project, skills = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "all",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
        ],
    )
    assert result.exit_code == 0, result.output
    for h in ("claude", "codex", "opencode", "cursor", "agents"):
        assert f"# plan: {h}" in result.output


def test_unknown_harness_exits_nonzero(tmp_path):
    home, project, skills = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "bogus",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
        ],
    )
    assert result.exit_code == 2
    assert "unknown harness" in result.output.lower()


def test_apply_and_uninstall_roundtrip(tmp_path):
    home, project, skills = _roots(tmp_path)
    apply = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply",
        ],
    )
    assert apply.exit_code == 0, apply.output
    assert (project / "AGENTS.md").exists()
    assert (project / ".kg-install-manifest.json").exists()
    uninstall = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--uninstall",
        ],
    )
    assert uninstall.exit_code == 0, uninstall.output
    assert not (project / "AGENTS.md").exists()
    assert not (project / ".kg-install-manifest.json").exists()


def test_apply_writes_manifest_path(tmp_path):
    home, project, skills = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply",
        ],
    )
    assert result.exit_code == 0, result.output
    assert ".kg-install-manifest.json" in result.output


# --- conflict + drift exit nonzero -----------------------------------------


def test_dry_run_reports_conflict_no_write(tmp_path):
    home, project, skills = _roots(tmp_path)
    (project / "AGENTS.md").write_text("user content")
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply",
        ],
    )
    # First apply pre-exists the file with no marker — agents installer appends.
    assert result.exit_code == 0, result.output
    # Second harness targeting same AGENTS.md via codex should conflict on marker.
    codex = runner.invoke(
        app,
        [
            "install", "codex",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply",
        ],
    )
    assert codex.exit_code == 1


def test_uninstall_drift_exits_nonzero(tmp_path):
    home, project, skills = _roots(tmp_path)
    runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply",
        ],
    )
    (project / "AGENTS.md").write_text("mutated by user")
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--uninstall",
        ],
    )
    assert result.exit_code == 1
    assert "error [agents]:" in result.output
    assert (project / "AGENTS.md").read_text() == "mutated by user"


def test_uninstall_foreign_manifest_root_refuses_without_touching_file(tmp_path):
    home, project, skills = _roots(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    target = victim / "owned.txt"
    target.write_text("keep")
    manifest = common.InstallManifest(
        project_root=str(victim),
        harness=Harness.AGENTS,
        transaction_state=common.TransactionState.COMMITTED,
        artifacts=[common.InstalledArtifact(
            path=str(target),
            kind=common.ArtifactKind.OWNED_FILE,
            content_sha256=common.content_hash(target.read_bytes()),
        )],
    )
    (project / ".kg-install-manifest.json").write_text(
        json.dumps(manifest.to_dict())
    )

    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--uninstall",
        ],
    )

    assert result.exit_code == 2
    assert "project_root" in result.output
    assert target.read_text() == "keep"


def test_uninstall_wrong_harness_exits_nonzero(tmp_path):
    home, project, skills = _roots(tmp_path)
    runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply",
        ],
    )
    result = runner.invoke(
        app,
        [
            "install", "claude",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--uninstall",
        ],
    )
    assert result.exit_code == 2
    assert "belongs to" in result.output


def test_uninstall_missing_manifest_exits_nonzero(tmp_path):
    home, project, skills = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--uninstall",
        ],
    )
    assert result.exit_code == 2
    assert "error [agents]:" in result.output


def test_apply_and_uninstall_mutually_exclusive(tmp_path):
    home, project, skills = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply", "--uninstall",
        ],
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_apply_all_refuses_one_manifest_per_project(tmp_path):
    home, project, skills = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "all",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply",
        ],
    )
    assert result.exit_code == 1
    assert "one .kg-install-manifest.json" in result.output


def test_uninstall_all_refused(tmp_path):
    home, project, skills = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "all",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--uninstall",
        ],
    )
    assert result.exit_code == 2
    assert "per-harness" in result.output


def test_default_project_and_home_via_env(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".kg").mkdir()
    skills = _seed_skills(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--skills-src", str(skills),
        ],
    )
    assert result.exit_code == 0, result.output


def test_bad_project_root(tmp_path):
    home, _, skills = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(tmp_path / "missing"),
            "--home-root", str(home),
            "--skills-src", str(skills),
        ],
    )
    assert result.exit_code == 2


def test_missing_kg_root_rejected(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir(); project.mkdir()
    skills = _seed_skills(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
        ],
    )
    assert result.exit_code == 2
    assert "contain .kg" in result.output


def test_bad_skills_src(tmp_path):
    home, project, _ = _roots(tmp_path)
    result = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(tmp_path / "missing"),
        ],
    )
    assert result.exit_code == 2
    assert "skills_src" in result.output


def test_idempotent_dry_run_after_apply(tmp_path):
    home, project, skills = _roots(tmp_path)
    runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
            "--apply",
        ],
    )
    second = runner.invoke(
        app,
        [
            "install", "agents",
            "--project-root", str(project),
            "--home-root", str(home),
            "--skills-src", str(skills),
        ],
    )
    assert second.exit_code == 0, second.output
    assert "already installed" in second.output


def test_help_lists_each_harness(tmp_path):
    result = runner.invoke(app, ["install", "--help"])
    assert result.exit_code == 0
    for token in ("claude", "codex", "opencode", "cursor", "agents", "all"):
        assert token in result.output


def test_manifest_paths_printed_for_each_harness_apply(tmp_path):
    home, project, skills = _roots(tmp_path)
    for h in ("agents", "codex", "opencode", "cursor"):
        result = runner.invoke(
            app,
            [
                "install", h,
                "--project-root", str(project),
                "--home-root", str(home),
                "--skills-src", str(skills),
                "--apply",
            ],
        )
        assert result.exit_code == 0, result.output
        assert ".kg-install-manifest.json" in result.output
        # Clean up so next harness starts fresh; uninstall rolls back.
        runner.invoke(
            app,
            [
                "install", h,
                "--project-root", str(project),
                "--home-root", str(home),
                "--skills-src", str(skills),
                "--uninstall",
            ],
        )
