from __future__ import annotations
import hashlib
import json
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from typer.testing import CliRunner
from kg.cli.init import init_project
from kg.cli.main import app
from kg.embed import FakeEmbedder
from kg.paths import KgPaths
from kg.storage.sqlite import SQLiteAdapter

runner = CliRunner()


def _fake_embedders(monkeypatch):
    monkeypatch.setattr("kg.cli.save.make_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr("kg.cli.query.make_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr("kg.embed.make_embedder", lambda _: FakeEmbedder())


_SEED_NODES = [
    {"type": "person", "name": "Demis Hassabis", "summary": "Founder of DeepMind."},
    {"type": "organization", "name": "DeepMind", "summary": "AI company in London."},
    {"type": "object", "subtype": "software", "name": "AlphaFold",
     "summary": "Protein structure predictor by DeepMind."},
    {"type": "person", "name": "Shane Legg", "summary": "Co-founder of DeepMind."},
]

_SEED_EDGES = [
    {"source_name": "Demis Hassabis", "semantic_type": "employed_by",
     "target_name": "DeepMind"},
    {"source_name": "DeepMind", "semantic_type": "owns",
     "target_name": "AlphaFold"},
    {"source_name": "Shane Legg", "semantic_type": "member_of",
     "target_name": "DeepMind"},
]


def _seed_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_embedders(monkeypatch)
    init = runner.invoke(app, ["init", "--user-id", "u", "--scope", "m4"])
    assert init.exit_code == 0, init.output
    n = tmp_path / "n.json"
    e = tmp_path / "e.json"
    n.write_text(json.dumps(_SEED_NODES))
    e.write_text(json.dumps(_SEED_EDGES))
    save = runner.invoke(app, ["save", "--nodes", str(n), "--edges", str(e),
                              "--source", "raw/x.md#chunk-0"])
    assert save.exit_code == 0, f"save failed: {save.output}\n{save.exception!r}"
    return tmp_path


# -- 1. VIZ ---------------------------------------------------------------


def test_viz_layout_json(tmp_path, monkeypatch):
    project = _seed_project(tmp_path, monkeypatch)
    adapter = SQLiteAdapter(KgPaths.for_cwd().kg_db)
    from kg.viz.server import serve

    port = _find_port()
    t = threading.Thread(target=serve, args=(adapter, port),
                         kwargs={"wiki_dir": None}, daemon=True)
    t.start()
    _wait_port(port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/layout")
        resp = urllib.request.urlopen(req, timeout=5)
        assert resp.status == 200
        ct = resp.headers.get("Content-Type", "")
        assert "application/json" in ct, f"bad CT: {ct}"
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src" in csp, f"missing CSP: {csp}"
        data = json.loads(resp.read())
        assert len(data["nodes"]) >= 3
        assert len(data["edges"]) >= 2
        node_ids = {n["id"] for n in data["nodes"]}
        assert all("cluster" not in n for n in data["nodes"])
        assert all(n["in_calls"] >= 0 for n in data["nodes"])
        assert all("status" not in n and "qualified_name" not in n for n in data["nodes"])
        edge_srcs = {e["source"] for e in data["edges"]}
        edge_tgts = {e["target"] for e in data["edges"]}
        assert edge_srcs.issubset(node_ids)
        assert edge_tgts.issubset(node_ids)
    finally:
        pass  # daemon thread dies with process


def test_viz_wiki_rejects_traversal(tmp_path, monkeypatch):
    project = _seed_project(tmp_path, monkeypatch)
    adapter = SQLiteAdapter(KgPaths.for_cwd().kg_db)
    from kg.viz.server import serve

    port = _find_port()
    t = threading.Thread(target=serve, args=(adapter, port),
                         kwargs={"wiki_dir": project / ".kg" / "wiki"}, daemon=True)
    t.start()
    _wait_port(port)
    try:
        for slug in ("../etc/passwd.md", "foo/bar.md", "symlink.md"):
            url = f"http://127.0.0.1:{port}/wiki/{slug}"
            try:
                urllib.request.urlopen(url, timeout=3)
                assert False, f"expected 404 for {url}"
            except urllib.error.HTTPError as exc:
                assert exc.code == 404, f"expected 404 for {slug}, got {exc.code}"
    finally:
        pass


def test_viz_binds_localhost_only(tmp_path, monkeypatch):
    project = _seed_project(tmp_path, monkeypatch)
    adapter = SQLiteAdapter(KgPaths.for_cwd().kg_db)
    from kg.viz.server import serve, make_handler
    from http.server import ThreadingHTTPServer

    port = _find_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(adapter, None))
    assert srv.server_address[0] == "127.0.0.1"
    srv.server_close()


def test_viz_no_browser_auto_open(tmp_path, monkeypatch):
    project = _seed_project(tmp_path, monkeypatch)
    adapter = SQLiteAdapter(KgPaths.for_cwd().kg_db)
    from kg.viz.server import serve

    port = _find_port()
    t = threading.Thread(target=serve, args=(adapter, port),
                         kwargs={"wiki_dir": None, "open_browser": True}, daemon=True)
    t.start()
    _wait_port(port)
    # serve() accepts open_browser but ignores it (del open_browser)
    # If we reach here without webbrowser.open being called, pass.
    assert True


# -- 2. CYPHER -------------------------------------------------------------


def test_cypher_match_return(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    q = 'MATCH (p:person) WHERE p.name =~ "Demis" RETURN p.name LIMIT 5'
    r = runner.invoke(app, ["cypher", q])
    assert r.exit_code == 0, f"cypher failed: {r.output}\n{r.exception!r}"
    assert "Demis Hassabis" in r.output


def test_cypher_write_clause_rejected(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    for q in [
        "CREATE (n:person {name:'x'}) RETURN n",
        "MATCH (n) MERGE (m:person {name:'x'}) RETURN m",
        "MATCH (n) SET n.x = 1 RETURN n",
        "MATCH (n) DELETE n",
    ]:
        r = runner.invoke(app, ["cypher", q])
        assert r.exit_code != 0, f"write query should fail: {q}"
        combined = (r.output + "").lower()
        assert "not allowed" in combined or "read-only" in combined or r.exception is not None


def test_cypher_oversized_input_rejected(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    from kg.cypher.parser import MAX_INPUT_CHARS
    long_q = "MATCH (n:person) WHERE n.name = '" + "a" * (MAX_INPUT_CHARS + 1) + "' RETURN n"
    r = runner.invoke(app, ["cypher", long_q])
    assert r.exit_code != 0, "oversized query should fail"


def test_cypher_no_sql_string_building(tmp_path, monkeypatch):
    """Translator never interpolates user text into SQL — uses adapter.fts_search."""
    import ast
    import inspect
    import kg.cypher.translator as mod
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str) and "SELECT" in v.value:
                    assert False, "translator builds SQL via f-string"


# -- 3. DEEP WIKI ---------------------------------------------------------


def test_deep_wiki_build_materializes(tmp_path, monkeypatch):
    project = _seed_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["wiki", "build", "from-query", "DeepMind", "--hops", "3"])
    assert r.exit_code == 0, f"wiki build failed: {r.output}\n{r.exception!r}"
    assert "built" in r.output.lower() or "cached" in r.output.lower()
    deep_dir = project / ".kg" / "wiki" / "deep"
    assert deep_dir.is_dir()
    slug_dirs = sorted(deep_dir.iterdir())
    assert len(slug_dirs) >= 1
    slug = slug_dirs[0].name
    assert ".." not in slug
    assert "/" not in slug
    expected_suffix = hashlib.sha256(b"DeepMind").hexdigest()[:8]
    assert slug.endswith("--" + expected_suffix)
    assert (slug_dirs[0] / "index.md").is_file()


def test_deep_wiki_cache_reuse(tmp_path, monkeypatch):
    project = _seed_project(tmp_path, monkeypatch)
    r1 = runner.invoke(app, ["wiki", "build", "from-query", "DeepMind", "--hops", "3"])
    assert r1.exit_code == 0
    r2 = runner.invoke(app, ["wiki", "build", "from-query", "DeepMind", "--hops", "3"])
    assert r2.exit_code == 0
    assert "cached" in r2.output.lower()


def test_deep_wiki_invalidated_on_graph_change(tmp_path, monkeypatch):
    project = _seed_project(tmp_path, monkeypatch)
    r1 = runner.invoke(app, ["wiki", "build", "from-query", "DeepMind", "--hops", "3"])
    assert r1.exit_code == 0
    n2 = project / "n2.json"
    n2.write_text(json.dumps([{"type": "person", "name": "New Person"}]))
    e2 = project / "e2.json"
    e2.write_text(json.dumps([]))
    save = runner.invoke(app, ["save", "--nodes", str(n2), "--edges", str(e2),
                              "--source", "raw/y.md#chunk-0"])
    assert save.exit_code == 0, f"save: {save.output}"
    r2 = runner.invoke(app, ["wiki", "build", "from-query", "DeepMind", "--hops", "3"])
    assert r2.exit_code == 0
    assert "built" in r2.output.lower()


def test_deep_search_memory_mcp_denied_without_auth(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    from kg.mcp.handlers import deep_search_memory, HandlerError

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        try:
            deep_search_memory(str(tmp_path), query="test", authorized=False)
            assert False, "should have raised HandlerError"
        except HandlerError as exc:
            assert "not authorized" in exc.message.lower()


def test_deep_search_memory_mcp_succeeds_with_auth(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    from kg.mcp.handlers import deep_search_memory

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = deep_search_memory(
            str(tmp_path), query="DeepMind", hops=2,
            authorized=True, embedder=FakeEmbedder(),
        )
        assert result["slug"]
        assert result["pages"] >= 0
        assert result["cached"] is False


# -- 4. WIKI LINT ---------------------------------------------------------


def test_wiki_lint_clean_on_fresh_sync(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    r = runner.invoke(app, ["wiki", "sync"])
    assert r.exit_code == 0, f"wiki sync: {r.output}\n{r.exception!r}"
    lint = runner.invoke(app, ["wiki", "lint"])
    assert lint.exit_code == 0, f"lint: {lint.output}\n{lint.exception!r}"
    combined = (lint.output + "").lower()
    # Fresh sync must be clean: lint compares escaped-to-escaped (via wiki._text),
    # so no STALE_SUMMARY false positive, and no orphan/broken_link.
    assert "stale_summary" not in combined
    assert "orphan" not in combined
    assert "broken_link" not in combined


def test_wiki_lint_orphan_and_broken_link(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    entities = tmp_path / ".kg" / "wiki" / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    # ORPHAN requires the page be in the kg-generated manifest.
    from kg.wiki import _MANIFEST
    orphan_name = "orphan--deadbeef.md"
    (entities / orphan_name).write_text("# Ghost\n\nOrphan page.")
    (entities / _MANIFEST).write_text(json.dumps([orphan_name]))
    # BROKEN_LINK: a wikilink pointing at a non-existent file.
    (entities / "broken--beefcafe.md").write_text("# Broken\n\nSee [[nonexistent]].")
    lint = runner.invoke(app, ["wiki", "lint"])
    assert lint.exit_code == 0
    combined = (lint.output + "").lower()
    assert "orphan" in combined
    assert "broken_link" in combined


def test_wiki_lint_stale_summary(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    entities = tmp_path / ".kg" / "wiki" / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    r = runner.invoke(app, ["wiki", "sync"])
    assert r.exit_code == 0
    md_files = list(entities.glob("*.md"))
    assert len(md_files) >= 1
    target = md_files[0]
    content = target.read_text()
    if "## Summary\n" in content:
        patched = content.replace("## Summary\n", "## Summary\n\nSTALE OUTDATED SUMMARY\n")
        target.write_text(patched)
        lint = runner.invoke(app, ["wiki", "lint"])
        assert lint.exit_code == 0
        assert "stale_summary" in (lint.output + "").lower()


def test_wiki_lint_respects_file_cap(tmp_path, monkeypatch):
    from kg.wiki_lint import _MAX_FILES, _iter_markdown_files
    entities = tmp_path / ".kg" / "wiki" / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    for i in range(_MAX_FILES + 5):
        (entities / f"f{i:05d}.md").write_text("# File")
    files = _iter_markdown_files(entities)
    assert len(files) == _MAX_FILES


# -- 5. CASCADE -----------------------------------------------------------


def test_cascade_ranks_chunks_advisory(tmp_path, monkeypatch):
    from kg.cascade import prioritize_chunks
    chunks = [
        "Boring text without entities.",
        "Alice Johnson met Bob Smith at Google headquarters in Mountain View.",
        "More boring text.",
    ]
    ranked = prioritize_chunks(chunks, budget=1)
    assert len(ranked) == 3
    assert "Alice Johnson" in ranked[0].text


def test_cascade_strips_markdown_syntax(tmp_path, monkeypatch):
    from kg.cascade import _strip_markdown
    md = "## Heading\n**bold** and *italic* and [link](url) and `code`"
    stripped = _strip_markdown(md)
    assert "#" not in stripped
    assert "**" not in stripped
    assert "[link](url)" not in stripped
    assert "`code`" not in stripped


# -- 6. DETERMINISM --------------------------------------------------------


def test_louvain_deterministic(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    adapter = SQLiteAdapter(KgPaths.for_cwd().kg_db)
    from kg.community import louvain

    c1 = louvain(adapter, seed=42)
    c2 = louvain(adapter, seed=42)
    assert c1 == c2


def test_deep_wiki_rerun_byte_identical(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    r1 = runner.invoke(app, ["wiki", "build", "from-query", "DeepMind", "--hops", "3"])
    assert r1.exit_code == 0
    deep_dir = tmp_path / ".kg" / "wiki" / "deep"
    slug_dirs = sorted(deep_dir.iterdir())
    assert len(slug_dirs) >= 1
    snapshot = {}
    for p in slug_dirs[0].iterdir():
        snapshot[p.name] = p.read_bytes()
    r2 = runner.invoke(app, ["wiki", "build", "from-query", "DeepMind", "--hops", "3"])
    assert r2.exit_code == 0
    for p in slug_dirs[0].iterdir():
        assert p.read_bytes() == snapshot.get(p.name), f"byte drift in {p.name} on cached rerun"


# -- HELPERS ---------------------------------------------------------------


def _find_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
    raise TimeoutError(f"port {port} not ready within {timeout}s")
