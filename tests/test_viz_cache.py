import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import kg.community as community
from kg.ontology import Edge, Node
from kg.storage.sqlite import SQLiteAdapter
from kg.viz.api import build_layout_payload
from kg.viz.server import make_handler


def _adapter(tmp_path):
    adapter = SQLiteAdapter(tmp_path / "kg.db")
    adapter.upsert_nodes([
        Node(id="a", type="person", name="A"),
        Node(id="b", type="person", name="B"),
    ])
    adapter.upsert_edges([Edge(id="a|knows|b", semantic_type="knows")])
    return adapter


def test_louvain_cache_reuses_and_invalidates_on_generation(tmp_path, monkeypatch):
    adapter = _adapter(tmp_path)
    calls = 0
    original = community.louvain

    def spy(adapter):
        nonlocal calls
        calls += 1
        return original(adapter)

    monkeypatch.setattr(community, "louvain", spy)
    community.louvain_cached(adapter)
    community.louvain_cached(adapter)
    assert calls == 1
    adapter.upsert_nodes([Node(id="c", type="person", name="C")])
    community.louvain_cached(adapter)
    assert calls == 2


def test_layout_payload_filters_active_rows_and_follows_contract(tmp_path):
    adapter = _adapter(tmp_path)
    payload = build_layout_payload(adapter)
    assert payload["total_nodes"] == 2
    assert len(payload["nodes"]) == 2
    assert [n["kg_id"] for n in payload["nodes"]] == ["a", "b"]
    assert payload["truncated_nodes"] is False
    assert payload["truncated_edges"] is False
    assert all(
        {"id", "kg_id", "x", "y", "z", "label", "name", "size", "color", "in_calls"}
        <= set(n) for n in payload["nodes"]
    )
    assert all("status" not in n and "qualified_name" not in n for n in payload["nodes"])

    # Tombstoning excludes the node from both totals and the payload.
    adapter.upsert_nodes([Node(id="a", type="person", name="A", status="tombstoned")])
    payload = build_layout_payload(adapter)
    assert payload["total_nodes"] == 1
    assert [n["kg_id"] for n in payload["nodes"]] == ["b"]


def test_graph_etag_returns_304(tmp_path):
    adapter = _adapter(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(adapter, None))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/graph.json"
    try:
        with urlopen(url) as response:
            etag = response.headers["ETag"]
        try:
            urlopen(Request(url, headers={"If-None-Match": etag}))
        except HTTPError as error:
            assert error.code == 304
            assert error.read() == b""
        else:
            raise AssertionError("expected HTTP 304")
    finally:
        server.shutdown()
        server.server_close()
