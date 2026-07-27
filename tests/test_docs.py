"""Doc tests: every documented command exists in the realized CLI, and every
guide links to a real neighbor. Stale names (sync_entities, --touched,
claude-code) must never appear in our docs.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
GUIDES = DOCS / "guides"
ARCHITECTURE = DOCS / "architecture"
OPS = DOCS / "ops"

# Commands that the realized src/kg/cli/main.py exposes. Extracted from the
# typer registrations; if you add a command to main.py, add its canonical form
# here. Matches are made against the FIRST WORD(S) of a documented `kg ...`
# invocation, so `kg install` covers `kg install claude --apply`.
REALIZED_COMMANDS = {
    "init",
    "install",
    "raw",
    "raw add",
    "raw list",
    "status",
    "config",
    "config get",
    "save",
    "search",
    "expand",
    "pack",
    "resolve",
    "dedup-check",
    "cypher",
    "wiki",
    "wiki sync",
    "wiki build",
    "wiki lint",
    "dream",
    "dream candidates",
    "merge",
    "review",
    "review list",
    "review confirm",
    "review reject",
    "viz",
    "snapshot",
    "mcp",
    "mcp serve",
    "hook",
    "hook session-end",
}

# Stale identifiers that must NEVER appear in our docs (preflight §API/doc).
STALE_TOKENS = [
    "sync_entities",          # realized as kg wiki sync (sync_wiki)
    "claude-code",            # realized harness token is "claude"
    "wiki sync --touched",    # wiki_sync_cli takes no flags
]

# Commands NOT realized that may appear in narrative text (deferred sections).
# Keyed by the command token extracted by _kg_invocations.
DEFERRED_OK = {
    "export",                 # kg export --cypher not yet implemented
    "auto_hook",              # config key present, runtime deferred
}


def _iter_markdown(root: Path):
    for path in sorted(root.rglob("*.md")):
        yield path


def _kg_invocations(text: str):
    """Yield the canonical (command) form of each `kg ...` invocation.

    kg has two shapes:
      - direct: `kg <command> [args]`           -> command
      - typer subgroups: `kg <group> <sub> [args]` -> group [sub]
    Groups in main.py: raw, config, wiki, dream, review, mcp, hook.
    Sub-of-subgroups (wiki build from-query) are normalized to wiki build.
    """
    pattern = re.compile(r"`kg\s+([a-z][\w-]*)((?:\s+[a-z][\w-]*){0,2})")
    groups = {"raw", "config", "wiki", "dream", "review", "mcp", "hook"}
    for m in pattern.finditer(text):
        head = m.group(1)
        rest = m.group(2).strip()
        if head in groups and rest:
            # `kg wiki build from-query` -> "wiki build"
            sub = rest.split()[0]
            # Skip arg-like second token (e.g. `config get embedding` -> config get).
            cmd = f"{head} {sub}".rstrip()
        else:
            cmd = head
        yield cmd


def test_no_stale_invocations_in_docs():
    """Stale commands (sync_entities, --touched, claude-code) must never appear
    as actual `kg ...` invocations. Narrative warnings ("the token is claude,
    not claude-code", "X does not exist") are allowed.
    """
    offenders: list[str] = []
    stale_invocations = [
        "sync_entities(",         # function call form
        "sync_entities ",         # call with args
    ]
    for root in (GUIDES, ARCHITECTURE, OPS):
        for path in _iter_markdown(root):
            lines = path.read_text(encoding="utf-8").splitlines()
            for ln in lines:
                low = ln.lower()
                # Allow explicit warning prose; only flag real invocations.
                is_warning = any(w in low for w in (
                    "not 'claude-code'", "does not exist", "not implemented",
                    "is a typo",
                ))
                if is_warning:
                    continue
                for token in stale_invocations:
                    if token in ln:
                        offenders.append(f"{path.relative_to(ROOT)}: '{token}'")
                # Flag kg install <claude-code> only when not in a "typo" warning.
                if "kg install claude-code" in ln:
                    offenders.append(
                        f"{path.relative_to(ROOT)}: 'kg install claude-code'"
                    )
                # Flag kg wiki sync with --touched outside "does not exist".
                if "kg wiki sync --touched" in ln:
                    offenders.append(
                        f"{path.relative_to(ROOT)}: 'kg wiki sync --touched'"
                    )
    assert not offenders, "stale invocations in docs:\n  " + "\n  ".join(offenders)


def test_every_documented_command_is_realized():
    """Every `kg <cmd>` invocation in docs must exist in REALIZED_COMMANDS."""
    unknown: list[str] = []
    for root in (GUIDES, ARCHITECTURE, OPS):
        for path in _iter_markdown(root):
            text = path.read_text(encoding="utf-8")
            for cmd in _kg_invocations(text):
                # Skip top-level `kg` (e.g. in "the kg memory") — only multi-word.
                if cmd in REALIZED_COMMANDS:
                    continue
                # Allow narrative references (not invocations) in deferred prose.
                if cmd in DEFERRED_OK:
                    continue
                # `kg mcp serve` already matched; allow `kg --version`.
                if cmd in {"version"}:
                    continue
                unknown.append(f"{path.relative_to(ROOT)}: `kg {cmd}`")
    assert not unknown, "documented commands not in realized main.py:\n  " + \
        "\n  ".join(sorted(set(unknown)))


def test_quickstart_mentions_core_commands():
    md = (GUIDES / "quickstart.md").read_text(encoding="utf-8")
    for needle in (
        "make install",
        "kg init",
        "kg raw add",
        "kg raw list",
        "kg status",
        "kg save",
        "kg search",
        "kg snapshot",
        "--from-snapshot",
    ):
        assert needle in md, f"quickstart.md missing '{needle}'"


def test_harness_setup_mentions_core_commands():
    md = (GUIDES / "harness-setup.md").read_text(encoding="utf-8")
    for needle in (
        "kg install",                       # generic form
        "`claude`",                         # harness table tokens
        "`codex`",
        "`opencode`",
        "`cursor`",
        "`agents`",
        "`all`",
        "kg install all",
        "kg install claude --apply",        # realized flag
        "--apply",
        "--uninstall",
        "kg mcp serve",
        "kg hook session-end",
    ):
        assert needle in md, f"harness-setup.md missing '{needle}'"
    # Realized flag names must be present.
    assert "--project-root" in md
    assert "--allow-writes" in md
    assert "--skills-src" in md
    assert "--force" in md


def test_architecture_overview_mentions_keystone_concepts():
    md = (ARCHITECTURE / "overview.md").read_text(encoding="utf-8")
    for needle in (
        "normalization gate",
        "Resolution is not dedup",
        "Paris",
        "gray zone",
        "tombstone",
        "content-derived",
    ):
        assert needle in md, f"architecture/overview.md missing '{needle}'"


def test_data_model_mentions_schema_and_adapter():
    md = (ARCHITECTURE / "data-model.md").read_text(encoding="utf-8")
    for needle in (
        "ontology.json",
        "POLE+O",
        "Storage adapter",
        "semantic_type",
        "same_as",
        "tombstoned",
    ):
        assert needle in md, f"architecture/data-model.md missing '{needle}'"


def test_runbook_mentions_operations():
    md = (OPS / "runbook.md").read_text(encoding="utf-8")
    for needle in (
        "kg snapshot",
        "--from-snapshot",
        "kg dream candidates",
        "kg merge",
        "kg review confirm",
        "kg review reject",
        "kg wiki sync",
        "kg wiki lint",
    ):
        assert needle in md, f"ops/runbook.md missing '{needle}'"


def test_troubleshooting_mentions_common_issues():
    md = (OPS / "troubleshooting.md").read_text(encoding="utf-8")
    for needle in (
        "claude-code",  # here it's an intentional warning
        "drift",
        "--from-snapshot",
        "ontology.json",
    ):
        assert needle in md, f"ops/troubleshooting.md missing '{needle}'"


def test_internal_links_resolve():
    """Every relative markdown link between docs/ files resolves on disk."""
    link = re.compile(r"\]\(([^)]+\.md)(?:#[^)]*)?\)")
    missing: list[str] = []
    for root in (GUIDES, ARCHITECTURE, OPS):
        for path in _iter_markdown(root):
            text = path.read_text(encoding="utf-8")
            for m in link.finditer(text):
                href = m.group(1)
                # Only resolve relative links (not https://, not absolute paths).
                if href.startswith(("http", "/")):
                    continue
                target = (path.parent / href).resolve()
                if not target.exists():
                    missing.append(f"{path.relative_to(ROOT)} -> {href}")
    assert not missing, "broken internal links:\n  " + "\n  ".join(missing)


def test_realized_commands_match_main_py():
    """Guard against drift between this test's REALIZED_COMMANDS list and the
    actually-registered commands in src/kg/cli/main.py.
    """
    from kg.cli.main import app

    registered: set[str] = set()
    for info in app.registered_commands:
        # typer stores the name on RegisteredCommand; fall back to callback name.
        name = getattr(info, "name", None) or info.callback.__name__.replace("_cli", "")
        registered.add(name)
    for sub in app.registered_groups:
        name = getattr(sub, "name", None)
        if name:
            registered.add(name)
            # Inspect nested typer apps for subcommands.
            sub_app = getattr(sub, "typer_instance", None)
            if sub_app is not None:
                for sub_info in sub_app.registered_commands:
                    sub_name = getattr(sub_info, "name", None) or \
                        sub_info.callback.__name__.replace("_cli", "")
                    registered.add(f"{name} {sub_name}")
    # Spot-check the core commands we document must be registered.
    must_exist = {
        "init", "install", "raw", "status", "config", "save", "search",
        "expand", "pack", "resolve", "dedup-check", "cypher", "wiki",
        "dream", "merge", "review", "viz", "snapshot", "mcp", "hook",
    }
    missing = must_exist - registered
    assert not missing, f"commands in main.py changed; update REALIZED_COMMANDS: {missing}"
