import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter
from kg.viz.indexing import IndexManager
from kg.viz.server import make_handler


def _assets(tmp_path):
    assets = tmp_path / "assets"
    (assets / "assets").mkdir(parents=True)
    (assets / "index.html").write_text("<html>kg</html>", encoding="utf-8")
    (assets / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (assets / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    return assets


def _adapter(tmp_path):
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    adapter.upsert_nodes([
        Node(id="u:person:a", type="person", name="A"),
        Node(id="u:organization:b", type="organization", name="B"),
    ])
    adapter.upsert_edges([Edge(id="u:person:a|knows|u:organization:b", semantic_type="knows")])
    return adapter


@pytest.fixture
def http_server(tmp_path):
    adapter = _adapter(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(adapter, None, assets=_assets(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base
    server.shutdown()
    server.server_close()


class _Response:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def json(self):
        return json.loads(self._body)


def get(base, path):
    try:
        with urlopen(base + path) as response:
            return _Response(response.status, response.headers, response.read())
    except HTTPError as exc:
        return _Response(exc.code, exc.headers, exc.read())


def test_live_routes_are_self_contained(http_server):
    assert get(http_server, "/").status == 200
    assert get(http_server, "/assets/app.js").headers["Content-Type"].startswith("text/javascript")
    assert get(http_server, "/api/capabilities").json()["graph"] is True
    assert get(http_server, "/api/capabilities").json()["adr"] is False
    layout = get(http_server, "/api/layout?max_nodes=2000").json()
    assert {"nodes", "edges", "total_nodes"} <= layout.keys()


def test_spa_asset_traversal_is_rejected(http_server):
    assert get(http_server, "/assets/../../pyproject.toml").status in (400, 404)


def test_index_validation_and_busy_status(http_server, tmp_path):
    from kg.viz.indexing import IndexManager

    def runner(root, name):
        import time
        time.sleep(0.5)

    server = http_server
    manager = IndexManager(runner)
    # Replace the fixture's handler manager: build a fresh server with the blocking runner.
    adapter = _adapter(tmp_path / "busy")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(adapter, None, index_manager=manager))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        bad = _post_json(base, "/api/index", {"root_path": "relative", "project_name": "x"})
        assert bad.status == 400
        accepted = _post_json(base, "/api/index", {"root_path": str(tmp_path), "project_name": "x"})
        assert accepted.status == 202
        busy = _post_json(base, "/api/index", {"root_path": str(tmp_path), "project_name": "y"})
        assert busy.status == 429
    finally:
        srv.shutdown()
        srv.server_close()


def _post_json(base, path, payload):
    req = Request(base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(req) as response:
            return _Response(response.status, response.headers, response.read())
    except HTTPError as exc:
        return _Response(exc.code, exc.headers, exc.read())


def test_csp_allows_required_local_threejs_features(http_server):
    csp = get(http_server, "/").headers["Content-Security-Policy"]
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp
    assert "worker-src 'self' blob:" in csp
    assert "object-src 'none'" in csp


def test_rpc_allowlist_and_mcp_wrap(http_server):
    rpc = _post_json(http_server, "/rpc", {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "list_projects", "arguments": {}},
    })
    assert rpc.status == 200
    body = rpc.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 7
    assert "result" in body and "content" in body["result"]
    projects = json.loads(body["result"]["content"][0]["text"])
    assert isinstance(projects["projects"], list)

    schema = _post_json(http_server, "/rpc", {
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "get_graph_schema", "arguments": {}},
    })
    assert schema.status == 200
    assert "node_labels" in json.loads(schema.json()["result"]["content"][0]["text"])

    bad = _post_json(http_server, "/rpc", {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "save_pole", "arguments": {}},
    })
    assert bad.status == 200
    assert bad.json()["error"]["code"] == -32601

    other = _post_json(http_server, "/rpc", {
        "jsonrpc": "2.0", "id": 10, "method": "tools/list",
    })
    assert other.status == 200
    assert other.json()["error"]["code"] == -32601


def test_layout_etag_and_304(http_server):
    first = get(http_server, "/api/layout?max_nodes=2000")
    etag = first.headers["ETag"]
    assert etag
    req = Request(http_server + "/api/layout?max_nodes=2000", headers={"If-None-Match": etag})
    try:
        with urlopen(req):
            raise AssertionError("expected 304")
    except HTTPError as exc:
        assert exc.code == 304
        assert exc.read() == b""
        assert exc.headers["ETag"] == etag


def test_layout_clamps_and_rejects(http_server):
    layout = get(http_server, "/api/layout?max_nodes=99999").json()
    assert layout["total_nodes"] == 2
    assert get(http_server, "/api/layout?max_nodes=0").status == 400
    assert get(http_server, "/api/layout?max_nodes=abc").status == 400


def test_repo_info_and_ui_config(http_server):
    info = get(http_server, "/api/repo-info").json()
    assert "root_path" in info and "branch" in info and "remote_url" in info
    config = get(http_server, "/api/ui-config").json()
    assert config["lang"] == "en"
    assert config["upstream_issues_url"].startswith("https://")


def test_no_browse_or_adr_routes(http_server):
    assert get(http_server, "/api/browse?path=/usr").status == 404
    assert get(http_server, "/api/adr").status == 404
    assert get(http_server, "/api/logs").status == 404
    assert get(http_server, "/api/processes").status == 404


def test_unknown_tool_rpc_and_bad_body(http_server):
    bad = _post_json(http_server, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "get_code_snippet", "arguments": {}}})
    assert bad.status == 200
    assert bad.json()["error"]["code"] == -32601
    not_json = Request(http_server + "/rpc", data=b"not json", headers={"Content-Type": "application/json"})
    try:
        urlopen(not_json)
        raise AssertionError("expected 400")
    except HTTPError as exc:
        assert exc.code == 400
