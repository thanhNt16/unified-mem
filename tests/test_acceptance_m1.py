"""M1 E2E: M0 ingest → hand-extracted nodes.json → kg save → kg search → expand.
Mirrors spec §6.3 + §7 (gate + read path), without the LLM (extraction is faked)."""
import json

from typer.testing import CliRunner

from kg.cli.main import app
from kg.embed import FakeEmbedder
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter


def _fake_embedders(monkeypatch):
    monkeypatch.setattr("kg.cli.save.make_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr("kg.cli.query.make_embedder", lambda _: FakeEmbedder())


def test_m1_gate_and_query(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    r = CliRunner()

    init = r.invoke(app, ["init", "--user-id", "u", "--scope", "demo"])
    assert init.exit_code == 0, init.output

    (tmp_path / "n.json").write_text(json.dumps([
        {"type": "person", "name": "Demis Hassabis", "summary": "Founder of DeepMind."},
        {"type": "organization", "name": "DeepMind", "summary": "AI company in London."},
        {"type": "object", "subtype": "software", "name": "AlphaFold",
         "summary": "Protein structure predictor by DeepMind."},
    ]))
    (tmp_path / "e.json").write_text(json.dumps([
        {"source_name": "Demis Hassabis", "semantic_type": "employed_by", "target_name": "DeepMind"},
        {"source_name": "DeepMind", "semantic_type": "owns", "target_name": "AlphaFold"},
    ]))

    save = r.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                          "--edges", str(tmp_path / "e.json"),
                          "--source", "raw/x.md#chunk-0"])
    assert save.exit_code == 0, f"exc={save.exception!r}\n{save.output}"
    assert "NEW" in save.stdout

    ad = SQLiteAdapter(KgPaths.for_cwd().kg_db)
    counts = ad.count()
    assert counts["nodes"] == 3
    assert counts["edges"] == 2

    # idempotent re-save → no new nodes/edges
    save2 = r.invoke(app, ["save", "--nodes", str(tmp_path / "n.json"),
                           "--edges", str(tmp_path / "e.json"),
                           "--source", "raw/x.md#chunk-0"])
    assert save2.exit_code == 0, f"exc={save2.exception!r}\n{save2.output}"
    assert "RESOLVED" in save2.stdout or "MERGED" in save2.stdout

    ad2 = SQLiteAdapter(KgPaths.for_cwd().kg_db)
    counts2 = ad2.count()
    assert counts2["nodes"] == 3
    assert counts2["edges"] == 2

    # query lands on Demis
    search = r.invoke(app, ["search", "DeepMind founder"])
    assert search.exit_code == 0, f"exc={search.exception!r}\n{search.output}"
    assert "u:person:demis-hassabis" in search.stdout

    # 2-hop expand from Demis reaches AlphaFold
    expand = r.invoke(app, ["expand", "u:person:demis-hassabis", "--hops", "2"])
    assert expand.exit_code == 0, f"exc={expand.exception!r}\n{expand.output}"
    assert "alphafold" in expand.stdout.lower()
