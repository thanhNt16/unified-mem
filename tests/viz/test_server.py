import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter
from kg.viz.server import _resolve_wiki_page, make_handler


def _assets(tmp_path):
    assets = tmp_path / "assets"
    (assets / "assets").mkdir(parents=True)
    (assets / "index.html").write_text("<html>kg</html>", encoding="utf-8")
    (assets / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (assets / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    return assets


def _server(adapter, wiki_dir=None, **kwargs):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(adapter, wiki_dir, **kwargs))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _adapter(tmp_path):
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    adapter.upsert_nodes([
        Node(id="u:person:a", type="person", name="<img src=x onerror=1>", summary="<script>x</script>"),
        Node(id="u:organization:b", type="organization", name="B"),
        Node(id="u:person:dead", type="person", name="Dead", status="tombstoned"),
    ])
    adapter.upsert_edges([
        Edge(id="u:person:a|knows|u:organization:b", semantic_type="knows"),
        Edge(id="u:person:a|knows|u:person:dead", semantic_type="knows"),
    ])
    return adapter


def test_layout_active_shape_csp_and_localhost(tmp_path):
    server, url = _server(_adapter(tmp_path), assets=_assets(tmp_path))
    try:
        assert server.server_address[0] == "127.0.0.1"
        with urlopen(url + "/api/layout?max_nodes=2000") as response:
            body = json.load(response)
            assert response.headers.get_content_type() == "application/json"
            assert response.headers["Content-Security-Policy"]
        assert {node["kg_id"] for node in body["nodes"]} == {"u:person:a", "u:organization:b"}
        assert all("cluster" not in node for node in body["nodes"])
        assert len(body["edges"]) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_csp_present_on_page_and_layout_json(tmp_path):
    server, url = _server(_adapter(tmp_path), assets=_assets(tmp_path))
    try:
        with urlopen(url + "/") as response:
            page = response.read().decode()
            csp = response.headers["Content-Security-Policy"]
            assert response.headers.get_content_type().startswith("text/html")
        assert "https://" not in page
        assert "<script>" not in page
        assert csp
    finally:
        server.shutdown()
        server.server_close()


def test_wiki_rejects_traversal_and_symlink_escape(tmp_path):
    wiki = tmp_path / "wiki"
    entities = wiki / "entities"
    entities.mkdir(parents=True)
    good = entities / "good.md"
    good.write_text("good", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (entities / "link.md").symlink_to(outside)
    assert _resolve_wiki_page(wiki, "good.md") == good.resolve()
    assert _resolve_wiki_page(wiki, "../outside.md") is None
    assert _resolve_wiki_page(wiki, "link.md") is None


def test_wiki_http_traversal_is_not_served(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "entities").mkdir(parents=True)
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
    server, url = _server(_adapter(tmp_path), wiki)
    try:
        with pytest.raises(HTTPError) as exc:
            urlopen(url + "/wiki/../secret.md")
        assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_no_webbrowser_dependency():
    import kg.viz.server as module
    assert "webbrowser" not in module.__dict__
