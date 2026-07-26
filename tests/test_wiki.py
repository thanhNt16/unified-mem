from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from kg.cli.init import init_project
from kg.cli.main import app
from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter
from kg.wiki import _entity_filename, sync_wiki


def _seed(tmp_path, nodes, edges=()):
    paths = init_project(tmp_path, user_id="u", scope="s")
    adapter = SQLiteAdapter(paths.kg_db)
    adapter.upsert_nodes(nodes)
    adapter.upsert_edges(list(edges))
    return paths, adapter


def test_basic_active_page_and_links(tmp_path):
    alice = Node(id="u:person:alice", type="person", name="Alice", summary="Founder",
                 sources=[{"doc": "raw/x.md", "chunk": 0}])
    bob = Node(id="u:person:bob", type="person", name="Bob")
    edge = Edge(id="u:person:alice|knows|u:person:bob", semantic_type="knows")
    paths, adapter = _seed(tmp_path, [alice, bob], [edge])
    assert sync_wiki(adapter, paths.wiki).pages_written == 2
    page = (paths.wiki / "entities" / _entity_filename(alice)).read_text()
    assert "Founder" in page
    assert _entity_filename(bob) in page


def test_tombstoned_excluded(tmp_path):
    active = Node(id="u:person:active", type="person", name="Active")
    dead = Node(id="u:person:dead", type="person", name="Dead", status="tombstoned")
    paths, adapter = _seed(tmp_path, [active, dead])
    assert sync_wiki(adapter, paths.wiki).pages_written == 1
    assert (paths.wiki / "entities" / _entity_filename(active)).exists()
    assert not (paths.wiki / "entities" / _entity_filename(dead)).exists()


def test_same_name_and_non_ascii_names_are_distinct_nonempty(tmp_path):
    first = Node(id="u:person:one", type="person", name="Same")
    second = Node(id="u:person:two", type="person", name="Same")
    non_ascii = Node(id="u:person:three", type="person", name="東京")
    paths, adapter = _seed(tmp_path, [first, second, non_ascii])
    sync_wiki(adapter, paths.wiki)
    files = [_entity_filename(node) for node in (first, second, non_ascii)]
    assert len(set(files)) == 3
    assert all(name and (paths.wiki / "entities" / name).read_text() for name in files)


def test_stale_generated_cleanup_keeps_user_file(tmp_path):
    node = Node(id="u:person:alice", type="person", name="Alice")
    paths, adapter = _seed(tmp_path, [node])
    sync_wiki(adapter, paths.wiki)
    entities = paths.wiki / "entities"
    user_file = entities / "user-note.md"
    user_file.write_text("# User note\n")
    adapter.upsert_nodes([node.model_copy(update={"status": "tombstoned"})])
    report = sync_wiki(adapter, paths.wiki)
    assert report.stale_removed == 1
    assert not (entities / _entity_filename(node)).exists()
    assert user_file.read_text() == "# User note\n"


def test_markdown_injection_is_escaped(tmp_path):
    payload = "# evil\n[x](javascript:evil) <script>alert(1)</script>"
    node = Node(id="u:person:evil", type="person", name=payload, summary=payload)
    paths, adapter = _seed(tmp_path, [node])
    sync_wiki(adapter, paths.wiki)
    page = (paths.wiki / "entities" / _entity_filename(node)).read_text()
    assert "\n# evil" not in page
    assert "[x](javascript:evil)" not in page
    assert "<script>" not in page


def test_sources_only_link_valid_raw_paths(tmp_path):
    node = Node(id="u:person:sources", type="person", name="Sources", sources=[
        {"doc": "../../etc/passwd"},
        {"doc": "https://evil.example"},
        {"doc": "raw/safe.md"},
    ])
    paths, adapter = _seed(tmp_path, [node])
    sync_wiki(adapter, paths.wiki)
    page = (paths.wiki / "entities" / _entity_filename(node)).read_text()
    assert "[../../etc/passwd]" not in page
    assert "[https://evil.example]" not in page
    assert "[raw/safe.md](../../raw/safe.md)" in page or "../../raw/safe.md" in page


def test_rerun_is_byte_identical(tmp_path):
    node = Node(id="u:person:alice", type="person", name="Alice")
    paths, adapter = _seed(tmp_path, [node])
    entities = paths.wiki / "entities"
    sync_wiki(adapter, paths.wiki)
    first = (entities / _entity_filename(node)).read_bytes()
    sync_wiki(adapter, paths.wiki)
    assert (entities / _entity_filename(node)).read_bytes() == first


def test_first_sync_refuses_generated_filename_owned_by_user(tmp_path):
    node = Node(id="u:person:alice", type="person", name="Alice")
    paths, adapter = _seed(tmp_path, [node])
    entities = paths.wiki / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    page = entities / _entity_filename(node)
    page.write_bytes(b"USER DATA")
    manifest = entities / ".kg-generated.json"
    manifest.write_bytes(b"[\"unrelated--00000000.md\"]\n")
    before = {path.name: path.read_bytes() for path in entities.iterdir()}

    with pytest.raises(FileExistsError, match="refusing to overwrite user wiki page"):
        sync_wiki(adapter, paths.wiki)

    assert {path.name: path.read_bytes() for path in entities.iterdir()} == before


def test_manifest_owned_generated_file_is_overwritten(tmp_path):
    node = Node(id="u:person:alice", type="person", name="Alice")
    paths, adapter = _seed(tmp_path, [node])
    entities = paths.wiki / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    page = entities / _entity_filename(node)
    page.write_text("OLD GENERATED DATA")
    (entities / ".kg-generated.json").write_text(json.dumps([page.name]))

    sync_wiki(adapter, paths.wiki)

    assert page.read_text() != "OLD GENERATED DATA"
    assert "# Alice" in page.read_text()


def test_source_paths_escape_labels_and_encode_targets(tmp_path):
    docs = [
        "raw/x](javascript:alert(1))",
        "raw/x\n# injected.md",
        "raw/x\x00.md",
        "raw/[brackets] (space)/東京.md",
    ]
    node = Node(id="u:person:sources", type="person", name="Sources",
                sources=[{"doc": doc} for doc in docs])
    paths, adapter = _seed(tmp_path, [node])
    sync_wiki(adapter, paths.wiki)
    page = (paths.wiki / "entities" / _entity_filename(node)).read_text()

    assert "\n# injected.md" not in page
    assert "javascript%3A" in page
    assert "\x00" not in page
    assert "../../raw/%5Bbrackets%5D%20%28space%29/%E6%9D%B1%E4%BA%AC.md" in page
    assert "[raw/\\[brackets\\] (space)/東京\\.md]" in page


def test_render_error_preserves_prior_pages_and_manifest(tmp_path, monkeypatch):
    node = Node(id="u:person:alice", type="person", name="Alice")
    paths, adapter = _seed(tmp_path, [node])
    entities = paths.wiki / "entities"
    sync_wiki(adapter, paths.wiki)
    old_page = (entities / _entity_filename(node)).read_bytes()
    old_manifest = (entities / ".kg-generated.json").read_bytes()
    monkeypatch.setattr("kg.wiki._render", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        sync_wiki(adapter, paths.wiki)
    assert (entities / _entity_filename(node)).read_bytes() == old_page
    assert (entities / ".kg-generated.json").read_bytes() == old_manifest


def test_cli_smoke(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--user-id", "u", "--scope", "s"]).exit_code == 0
    paths = init_project(tmp_path, user_id="u", scope="s")
    SQLiteAdapter(paths.kg_db).upsert_nodes([Node(id="u:person:alice", type="person", name="Alice")])
    result = runner.invoke(app, ["wiki", "sync"])
    assert result.exit_code == 0, result.stdout
    assert "synced 1 pages" in result.stdout
