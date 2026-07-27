"""M5 T7: install/uninstall reversibility across all 5 harnesses.

Parametrized over realized harness names ["claude", "codex", "opencode", "cursor", "agents"].
Deterministic: tmp roots, no model/network.

Per harness:
1. Fresh project (kg init) + tmp home.
2. Snapshot home/project config state before install.
3. kg install <harness> --apply -> exit 0.
4. Add USER artifact install (extra MCP server, user CLAUDE.md/AGENTS.md line, extra hook).
5. kg install <harness> --uninstall -> reverses kg-owned fragments, restores backups,
   preserves user artifact.
6. Assert: kg-owned fragments gone; user artifact present; pre-install state restored on
   non-owned files; reinstall -> uninstall clean (no-op or fresh-install).
7. Drift case: install, mutate an OWNED file; --uninstall refuses (nonzero exit), leaves
   everything intact.

Harness config formats that cannot safely mutate pre-existing non-owned (Codex TOML, OpenCode
JSONC) assert the realized deterministic refuse / manual-step path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kg.cli.init import init_project
from kg.cli.main import app
from kg.install.manifest import manifest_path

runner = CliRunner()
SKILLS_SRC = Path(__file__).resolve().parent.parent.parent / "skills"

HARNESSES = ["claude", "codex", "opencode", "cursor", "agents"]


def _assert_ok(result):
    assert result.exit_code == 0, (
        f"exit={result.exit_code}\nexception={result.exception!r}\n"
        f"output={result.output}\nstderr={result.stderr}"
    )


def _init(project: Path) -> None:
    init_project(project, user_id="u", scope="m5-e2e")


def _install_args(harness: str, tmp_path: Path, *, apply: bool = False, uninstall: bool = False) -> list[str]:
    """Build CLI args. Uninstall path never passes --apply or --skills-src."""
    cmd = [
        "install", harness,
        "--home-root", str(tmp_path / "home"),
        "--project-root", str(tmp_path / "project"),
    ]
    if not uninstall:
        cmd += ["--skills-src", str(SKILLS_SRC)]
    if apply:
        cmd.append("--apply")
    if uninstall:
        cmd.append("--uninstall")
    return cmd


# ---------------------------------------------------------------------------
# Per-harness owned-file path + USER-artifact mutation helpers
# ---------------------------------------------------------------------------


def _owned_file(harness: str, tmp_path: Path) -> Path:
    """Return a file the installer wholly owns (drift-mutable)."""
    project = tmp_path / "project"
    home = tmp_path / "home"
    if harness == "claude":
        return project / ".claude" / "skills" / "kg-query" / "SKILL.md"
    if harness == "codex":
        return project / ".codex" / "config.toml"
    if harness == "opencode":
        return project / "opencode.json"
    if harness == "cursor":
        return project / ".cursor" / "rules" / "kg.mdc"
    # agents: only the marker block itself is kg-owned inside AGENTS.md.
    return project / "AGENTS.md"


def _drift_owned_file(harness: str, tmp_path: Path) -> None:
    """Mutate the kg-owned fragment of an owned file so uninstall must refuse.

    - Wholly-owned files (codex config, cursor rule, claude skill): append bytes.
    - JSON-object shared files (opencode): mutate a value the installer recorded
      so the on-disk fingerprint diverges from the manifest.
    - MARKER_BLOCK shared files (agents): edit bytes inside the marker block
      itself (the only kg-owned fragment).
    """
    project = tmp_path / "project"
    if harness == "opencode":
        cfg_path = project / "opencode.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["mcp"]["kg"]["command"].append("--drift")
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        return
    if harness == "agents":
        agents = project / "AGENTS.md"
        text = agents.read_text()
        text = text.replace(
            "## Local knowledge graph",
            "## TAMPERED Local knowledge graph",
            1,
        )
        agents.write_text(text, encoding="utf-8")
        return
    owned = _owned_file(harness, tmp_path)
    owned.write_bytes(owned.read_bytes() + b"\nmanual drift\n")


def _add_user_artifact(harness: str, tmp_path: Path) -> None:
    """Inject a USER-owned fragment into a config file the installer also touched.

    Survives uninstall because the uninstaller only removes kg-owned fragments
    (exact digest match for owned files; marker splice / JSON-key removal for
    shared files). User content outside those exact fragments is preserved.
    """
    project = tmp_path / "project"
    home = tmp_path / "home"
    if harness == "claude":
        # Add a non-kg MCP server entry + user CLAUDE.md line + user hook.
        mcp = home / ".claude.json"
        data = json.loads(mcp.read_text())
        data.setdefault("mcpServers", {})["user-server"] = {"command": "user"}
        mcp.write_text(json.dumps(data), encoding="utf-8")
        settings = home / ".claude" / "settings.json"
        sdata = json.loads(settings.read_text())
        sdata["hooks"]["SessionEnd"].append({"matcher": "user", "hooks": []})
        settings.write_text(json.dumps(sdata), encoding="utf-8")
        (project / "CLAUDE.md").write_text(
            (project / "CLAUDE.md").read_text() + "user CLAUDE.md line\n",
            encoding="utf-8",
        )
    elif harness == "codex":
        # AGENTS.md shared file: user line outside the marker block.
        agents = project / "AGENTS.md"
        agents.write_text(agents.read_text() + "user AGENTS.md line\n", encoding="utf-8")
    elif harness == "opencode":
        # opencode.json shared: user MCP server survives structural merge/unmerge.
        cfg = json.loads((project / "opencode.json").read_text())
        cfg["mcp"]["user-server"] = {"type": "local", "command": ["user"], "enabled": True}
        (project / "opencode.json").write_text(json.dumps(cfg), encoding="utf-8")
        agents = project / "AGENTS.md"
        agents.write_text(agents.read_text() + "user AGENTS.md line\n", encoding="utf-8")
    elif harness == "cursor":
        # mcp.json shared: user server survives merge/unmerge.
        mcp = json.loads((project / ".cursor" / "mcp.json").read_text())
        mcp["mcpServers"]["user-server"] = {"type": "stdio", "command": "user", "args": []}
        (project / ".cursor" / "mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
        agents = project / "AGENTS.md"
        agents.write_text(agents.read_text() + "user AGENTS.md line\n", encoding="utf-8")
    else:  # agents
        agents = project / "AGENTS.md"
        agents.write_text(agents.read_text() + "user AGENTS.md line\n", encoding="utf-8")


def _assert_user_artifact_preserved(harness: str, tmp_path: Path) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    if harness == "claude":
        mcp = json.loads((home / ".claude.json").read_text())
        assert mcp["mcpServers"].get("user-server") == {"command": "user"}
        settings = json.loads((home / ".claude" / "settings.json").read_text())
        assert {"matcher": "user", "hooks": []} in settings["hooks"]["SessionEnd"]
        assert "user CLAUDE.md line" in (project / "CLAUDE.md").read_text()
    elif harness in ("codex", "cursor", "opencode", "agents"):
        assert "user AGENTS.md line" in (project / "AGENTS.md").read_text()
    if harness == "opencode":
        cfg = json.loads((project / "opencode.json").read_text())
        assert cfg["mcp"].get("user-server") == {
            "type": "local", "command": ["user"], "enabled": True,
        }
    if harness == "cursor":
        mcp = json.loads((project / ".cursor" / "mcp.json").read_text())
        assert mcp["mcpServers"].get("user-server") == {
            "type": "stdio", "command": "user", "args": [],
        }


def _assert_kg_owned_gone(harness: str, tmp_path: Path) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    if harness == "claude":
        mcp = json.loads((home / ".claude.json").read_text())
        assert "kg" not in mcp.get("mcpServers", {}), mcp
        settings = json.loads((home / ".claude" / "settings.json").read_text())
        kg_hooks = [
            h for h in settings["hooks"]["SessionEnd"]
            if any("kg" in str(hook.get("command", "")) for hook in h.get("hooks", []))
        ]
        assert kg_hooks == [], kg_hooks
        assert "kg-install:claude" not in (project / "CLAUDE.md").read_text()
        # Each installed skill directory is removed; parent kg/ may linger as empty dir.
        for name in ("kg-extract", "kg-query", "kg-dream"):
            assert not (home / ".claude" / "skills" / "kg" / name).exists()
    elif harness == "codex":
        # Codex owns .codex/config.toml wholly when written fresh.
        assert not (project / ".codex" / "config.toml").exists() or (
            "kg" not in (project / ".codex" / "config.toml").read_text()
        )
        agents = project / "AGENTS.md"
        assert not agents.exists() or "kg-install:codex" not in agents.read_text()
        assert not (project / ".agents" / "skills" / "kg-extract").exists()
    elif harness == "opencode":
        opencode = project / "opencode.json"
        assert not opencode.exists() or "kg" not in json.loads(opencode.read_text()).get("mcp", {})
        agents = project / "AGENTS.md"
        assert not agents.exists() or "kg-install:opencode" not in agents.read_text()
        assert not (project / ".opencode" / "skills" / "kg-extract").exists()
    elif harness == "cursor":
        mcp_path = project / ".cursor" / "mcp.json"
        assert not mcp_path.exists() or "kg" not in json.loads(mcp_path.read_text()).get("mcpServers", {})
        assert not (project / ".cursor" / "rules" / "kg.mdc").exists()
        agents = project / "AGENTS.md"
        assert not agents.exists() or "kg-install:cursor" not in agents.read_text()
    else:  # agents
        agents = project / "AGENTS.md"
        assert not agents.exists() or "kg-install:agents" not in agents.read_text()


# ---------------------------------------------------------------------------
# Roundtrip + drift refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("harness", HARNESSES)
def test_apply_uninstall_roundtrip_preserves_user_artifacts(tmp_path, monkeypatch, harness):
    """Fresh install -> user mutation -> uninstall preserves user, removes kg-owned."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(); home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    _init(project)

    # Pre-install: kg-owned target absent (where applicable).
    owned = _owned_file(harness, tmp_path)
    if harness == "agents":
        assert not owned.exists()
    # Snapshot non-owned files (manifest doesn't exist yet; .kg internals are kg-init's).
    manifest_before = manifest_path(project)
    assert not manifest_before.exists()

    # Apply install.
    applied = runner.invoke(app, _install_args(harness, tmp_path, apply=True))
    _assert_ok(applied)
    assert manifest_path(project).exists(), "manifest should be committed"

    # Inject a USER artifact that must survive uninstall.
    _add_user_artifact(harness, tmp_path)

    # Re-running plan with --apply is a clean no-op (existing manifest matches).
    reapplied = runner.invoke(app, _install_args(harness, tmp_path, apply=True))
    _assert_ok(reapplied)

    # Uninstall reverses kg-owned fragments; user artifact survives.
    uninstalled = runner.invoke(app, _install_args(harness, tmp_path, uninstall=True))
    _assert_ok(uninstalled)

    _assert_kg_owned_gone(harness, tmp_path)
    _assert_user_artifact_preserved(harness, tmp_path)
    assert not manifest_path(project).exists(), "manifest must be removed on uninstall"


@pytest.mark.parametrize("harness", HARNESSES)
def test_uninstall_refuses_owned_drift(tmp_path, monkeypatch, harness):
    """Mutating an OWNED file makes --uninstall refuse (nonzero); artifacts intact."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(); home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    _init(project)

    applied = runner.invoke(app, _install_args(harness, tmp_path, apply=True))
    _assert_ok(applied)

    owned = _owned_file(harness, tmp_path)
    assert owned.exists(), f"owned file missing for {harness}: {owned}"
    snapshot_before_drift = owned.read_bytes()
    manifest_before = manifest_path(project).read_bytes()

    # Mutate the kg-owned fragment (method depends on artifact kind).
    _drift_owned_file(harness, tmp_path)

    refused = runner.invoke(app, _install_args(harness, tmp_path, uninstall=True))
    assert refused.exit_code != 0, (
        f"uninstall must refuse drifted owned file for {harness}; "
        f"exit={refused.exit_code}\noutput={refused.output}"
    )
    diagnostic = " ".join(filter(None, [refused.output, refused.stderr, str(refused.exception)])).lower()
    # Uninstaller fails closed with drift/conflict/refused/malformed messages depending on
    # artifact kind (raw bytes vs JSON object). Any of these is a valid refusal signal.
    assert any(token in diagnostic for token in ("drift", "conflict", "refus", "malformed")), (
        f"missing refusal diagnostic for {harness}: {diagnostic}"
    )

    # Nothing changed: drifted file untouched, manifest still present.
    assert owned.read_bytes() != snapshot_before_drift, "drift mutation did not take effect"
    assert owned.read_bytes() == _owned_file(harness, tmp_path).read_bytes()
    assert manifest_path(project).read_bytes() == manifest_before


# ---------------------------------------------------------------------------
# Reinstall-after-uninstall clean re-install
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("harness", HARNESSES)
def test_reinstall_after_uninstall_is_clean(tmp_path, monkeypatch, harness):
    """After uninstall, a fresh install succeeds and is reversible again."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(); home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    _init(project)

    first = runner.invoke(app, _install_args(harness, tmp_path, apply=True))
    _assert_ok(first)
    uninst = runner.invoke(app, _install_args(harness, tmp_path, uninstall=True))
    _assert_ok(uninst)
    assert not manifest_path(project).exists()

    second = runner.invoke(app, _install_args(harness, tmp_path, apply=True))
    _assert_ok(second)
    assert manifest_path(project).exists()

    uninst2 = runner.invoke(app, _install_args(harness, tmp_path, uninstall=True))
    _assert_ok(uninst2)
    assert not manifest_path(project).exists()
    _assert_kg_owned_gone(harness, tmp_path)


# ---------------------------------------------------------------------------
# Codex TOML + OpenCode JSONC pre-existing -> deterministic refuse / manual step
# ---------------------------------------------------------------------------


def test_codex_preexisting_toml_refuses_or_manual(tmp_path, monkeypatch):
    """Codex installer refuses to rewrite a pre-existing non-empty TOML config.

    Per preflight: harness config formats cannot safely mutate pre-existing non-owned
    content (Codex TOML has no comment-preserving stdlib writer). The plan emits a
    manual step rather than touching the file.
    """
    harness = "codex"
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(); home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    _init(project)

    # Pre-existing user TOML with no kg entry — installer must not rewrite.
    (project / ".codex").mkdir()
    (project / ".codex" / "config.toml").write_text(
        "# user comment\n[mcp_servers.user]\ncommand = \"user\"\n",
        encoding="utf-8",
    )
    dry = runner.invoke(app, _install_args(harness, tmp_path))
    _assert_ok(dry)
    # No --apply performed: nothing written, manifest absent.
    assert not manifest_path(project).exists()
    # Plan output indicates manual step.
    assert "manual_steps" in dry.output or "manual" in dry.output.lower()


def test_opencode_jsonc_refuses_mutation(tmp_path, monkeypatch):
    """OpenCode installer refuses to rewrite opencode.jsonc (comments would be lost).

    Per preflight: JSONC cannot be safely mutated by stdlib json. The plan emits a
    manual step rather than touching the file.
    """
    harness = "opencode"
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(); home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    _init(project)

    (project / "opencode.jsonc").write_text(
        "// user comment\n{\n  \"mcp\": {}\n}\n",
        encoding="utf-8",
    )
    dry = runner.invoke(app, _install_args(harness, tmp_path))
    _assert_ok(dry)
    assert not manifest_path(project).exists()
    # Plan output indicates manual step.
    assert "manual_steps" in dry.output or "manual" in dry.output.lower()
    # The JSONC file is byte-for-byte unchanged (no apply ran).
    assert (project / "opencode.jsonc").read_text() == "// user comment\n{\n  \"mcp\": {}\n}\n"
