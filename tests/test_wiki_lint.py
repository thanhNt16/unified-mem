"""Tests for kg.wiki_lint."""
from __future__ import annotations

import json

from kg.cli.init import init_project
from kg.ontology import Node
from kg.storage.sqlite import SQLiteAdapter
from kg.wiki import _entity_filename, sync_wiki
from kg.wiki_lint import _MAX_FILES, lint


def _seed(tmp_path, nodes):
    paths = init_project(tmp_path, user_id="u", scope="s")
    adapter = SQLiteAdapter(paths.kg_db)
    adapter.upsert_nodes(nodes)
    return paths, adapter


def test_lint_flags_orphan_broken_link_and_stale_summary(tmp_path):
    alice = Node(id="u:person:alice", type="person", name="Alice", summary="Canonical")
    paths, adapter = _seed(tmp_path, [alice])
    sync_wiki(adapter, paths.wiki)
    entities = paths.wiki / "entities"
    alice_page = entities / _entity_filename(alice)
    alice_page.write_text(
        "# Alice\n\n## Summary\n\nOld summary\n\n## Links\n\n[[missing.md]]\n"
    )
    orphan = "orphan--00000000.md"
    (entities / orphan).write_text("# Old page\n")
    (entities / ".kg-generated.json").write_text(json.dumps([alice_page.name, orphan]))

    issues = lint(entities, adapter)
    assert {(i.kind, i.path) for i in issues} == {
        ("BROKEN_LINK", alice_page.name),
        ("STALE_SUMMARY", alice_page.name),
        ("ORPHAN", orphan),
    }


def test_lint_file_cap(tmp_path):
    paths, adapter = _seed(tmp_path, [])
    entities = paths.wiki / "entities"
    entities.mkdir(parents=True)
    for i in range(_MAX_FILES + 1):
        (entities / f"{i:05}.md").write_text("[[missing.md]]")

    issues = lint(entities, adapter)
    assert len(issues) == _MAX_FILES


def test_lint_output_deterministic(tmp_path):
    paths, adapter = _seed(tmp_path, [])
    entities = paths.wiki / "entities"
    entities.mkdir(parents=True)
    (entities / "z.md").write_text("[[missing-z.md]]")
    (entities / "a.md").write_text("[[missing-a.md]]")

    first = lint(entities, adapter)
    second = lint(entities, adapter)
    assert first == second
    assert [issue.path for issue in first] == ["a.md", "z.md"]


def test_lint_ignores_traversal_link(tmp_path):
    paths, adapter = _seed(tmp_path, [])
    entities = paths.wiki / "entities"
    entities.mkdir(parents=True)
    (entities / "note.md").write_text("[[../../etc/passwd]]")
    assert lint(entities, adapter) == []
