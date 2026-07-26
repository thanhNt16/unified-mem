"""End-to-end M0: init → add 3 sources → dedupe → list → status.
Mirrors spec §20 (files-layer portion)."""
from typer.testing import CliRunner
from kg.cli.main import app


def test_m0_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()

    assert r.invoke(app, ["init", "--user-id", "quan", "--scope", "demo"]).exit_code == 0
    assert (tmp_path / ".kg" / "config.toml").is_file()
    assert (tmp_path / ".kg" / "ontology.json").is_file()
    assert (tmp_path / ".kg" / "registry.jsonl").is_file()

    # three sources (one is a duplicate of another)
    (tmp_path / "a.md").write_text("# Alpha\n\nDeepMind makes AlphaFold.")
    (tmp_path / "b.md").write_text("# Bravo\n\nDemis Hassabis founded DeepMind.")
    assert r.invoke(app, ["raw", "add", str(tmp_path / "a.md")]).exit_code == 0
    assert r.invoke(app, ["raw", "add", str(tmp_path / "b.md")]).exit_code == 0
    assert r.invoke(app, ["raw", "add", "-", "--type", "text", "--title", "note"],
                    input="A pasted fact about RRF.").exit_code == 0

    # duplicate content → skipped
    dup = r.invoke(app, ["raw", "add", str(tmp_path / "a.md")])
    assert dup.exit_code == 0
    assert "skipped" in dup.stdout.lower() or "duplicate" in dup.stdout.lower()

    assert "3" in r.invoke(app, ["status"]).stdout
    listed = r.invoke(app, ["raw", "list"]).stdout
    assert "alpha" in listed.lower() or "bravo" in listed.lower()

    # chunk boundaries persisted in frontmatter
    from kg.registry import Registry
    from kg.paths import KgPaths
    from kg.frontmatter import parse
    e = Registry(KgPaths.for_cwd().registry).all()[0]
    fm, _ = parse((tmp_path / ".kg" / e.path).read_text(encoding="utf-8"))
    assert fm.chunks and fm.sha256


def test_m0_config_get_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(app, ["init", "--user-id", "q", "--scope", "s"])
    out = CliRunner().invoke(app, ["config", "get", "chunking.tokens"])
    assert "512" in out.stdout
