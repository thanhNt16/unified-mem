"""Loopback-only server for the packaged graph UI and read-only APIs."""
from __future__ import annotations

import json
import mimetypes
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit

from kg.storage.base import StorageAdapter
from kg.viz.api import (
    PayloadTooLarge,
    build_capabilities,
    build_layout_payload,
    build_project_payload,
    build_schema_payload,
    json_bytes,
)
from kg.viz.indexing import IndexBusy, IndexManager, InvalidProjectPath

_MAX_NODES = 2_000
_MAX_BODY = 64 * 1024
_CSP = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; "
    "script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; worker-src 'self' blob:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


def _resolve_wiki_page(wiki_dir: Path, raw_slug: str) -> Path | None:
    if not raw_slug or any(ord(c) < 32 for c in raw_slug) or "\\" in raw_slug or "\x00" in raw_slug:
        return None
    p = PurePosixPath(raw_slug.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or len(p.parts) != 1 or not p.name.endswith(".md"):
        return None
    entities = (Path(wiki_dir) / "entities").resolve(strict=False)
    target = (entities / p.name).resolve(strict=False)
    try:
        target.relative_to(entities)
    except ValueError:
        return None
    return target if target.is_file() and not target.is_symlink() else None


def _safe_json_error(code: str, message: str) -> bytes:
    return json_bytes({"error": {"code": code, "message": message}})


def _repo_info(root: Path) -> dict[str, str]:
    root = root.resolve()

    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True,
                                  check=False, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    branch = run("branch", "--show-current")
    remote = run("config", "--get", "remote.origin.url")
    if "@" in remote and "://" in remote:
        scheme, rest = remote.split("://", 1)
        remote = scheme + "://" + rest.rsplit("@", 1)[-1]
    remote = remote.removesuffix(".git").rstrip("/")
    web = remote
    if remote.startswith("git@"):
        web = "https://" + remote[4:].replace(":", "/")
    elif remote.startswith("ssh://"):
        web = "https://" + remote.removeprefix("ssh://").split("/", 1)[-1]
    web = web.rstrip("/")
    return {"root_path": str(root), "branch": branch, "remote_url": remote,
            "web_base": web, "blob_base": f"{web}/blob/{branch}" if web and branch else ""}


class _Handler(BaseHTTPRequestHandler):
    adapter_factory = None
    wiki_dir: Path | None = None
    assets = None
    index_manager: IndexManager | None = None
    project_name = ""
    project_root: Path | None = None

    def log_message(self, *_args):
        pass

    def _adapter(self):
        return type(self).adapter_factory()

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", _CSP)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: object, extra=None):
        self._send(status, json_bytes(payload), extra=extra)

    def _asset(self, path: str):
        raw = unquote(urlsplit(path).path)
        rel = raw.lstrip("/") or "index.html"
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts:
            return self._send(400, _safe_json_error("invalid_path", "invalid asset path"))
        try:
            resource = self.assets.joinpath(*p.parts)
            if not resource.is_file():
                if p.suffix:
                    return self._send(404, _safe_json_error("not_found", "not found"))
                resource = self.assets.joinpath("index.html")
                if not resource.is_file():
                    return self._send(404, _safe_json_error("not_found", "not found"))
            body = resource.read_bytes()
        except (OSError, TypeError):
            return self._send(404, _safe_json_error("not_found", "not found"))
        mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if mime.startswith("text/") or mime == "application/javascript":
            mime += "; charset=utf-8"
        self._send(200, body, mime)

    def do_GET(self):  # noqa: N802
        parsed = urlsplit(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path.startswith("/wiki/"):
            if self.wiki_dir is None:
                return self._send(404, _safe_json_error("not_found", "not found"))
            page = _resolve_wiki_page(self.wiki_dir, path[6:])
            if page is None:
                return self._send(404, _safe_json_error("not_found", "not found"))
            try:
                return self._send(200, page.read_bytes(), "text/markdown; charset=utf-8")
            except OSError:
                return self._send(404, _safe_json_error("not_found", "not found"))
        if path == "/api/layout":
            raw = query.get("max_nodes", [str(_MAX_NODES)])[0]
            try:
                limit = min(_MAX_NODES, int(raw, 10))
                if limit < 1:
                    raise ValueError
            except ValueError:
                return self._send(400, _safe_json_error("invalid_query", "max_nodes must be a positive decimal"))
            adapter = self._adapter()
            try:
                etag = f'"{adapter.generation()}"'
                if self.headers.get("If-None-Match") == etag:
                    return self._send(304, b"", extra={"ETag": etag})
                try:
                    payload = build_layout_payload(adapter, max_nodes=limit)
                except PayloadTooLarge:
                    return self._send(413, _safe_json_error("payload_too_large", "layout payload too large"))
                return self._json(200, payload, {"ETag": etag})
            finally:
                adapter.conn.close()
        if path == "/api/capabilities":
            return self._json(200, build_capabilities(static=False))
        if path == "/api/repo-info":
            return self._json(200, _repo_info(self.project_root or Path.cwd()))
        if path == "/api/ui-config":
            return self._json(200, {"lang": "en", "upstream_issues_url": "https://github.com/DeusData/codebase-memory-mcp/issues/new"})
        if path == "/api/index-status":
            return self._json(200, self.index_manager.status() if self.index_manager else [])
        if path == "/api/project":
            adapter = self._adapter()
            try:
                return self._json(200, build_project_payload(adapter, self.project_name))
            finally:
                adapter.conn.close()
        if path == "/api/schema":
            adapter = self._adapter()
            try:
                return self._json(200, build_schema_payload(adapter))
            finally:
                adapter.conn.close()
        if path.startswith("/api/"):
            return self._send(404, _safe_json_error("not_found", "not found"))
        return self._asset(path)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_BODY:
            return self._send(413, _safe_json_error("payload_too_large", "request body too large"))
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, OSError):
            return self._send(400, _safe_json_error("invalid_json", "invalid JSON body"))
        if self.path.split("?", 1)[0] == "/api/index":
            if not isinstance(body, dict) or not isinstance(body.get("root_path"), str) or not isinstance(body.get("project_name"), str):
                return self._send(400, _safe_json_error("invalid_body", "root_path and project_name are required strings"))
            if self.index_manager is None:
                return self._send(503, _safe_json_error("unavailable", "indexing unavailable"))
            try:
                job = self.index_manager.start(body["root_path"], body["project_name"])
            except InvalidProjectPath:
                return self._send(400, _safe_json_error("invalid_project", "invalid project path or name"))
            except IndexBusy:
                return self._send(429, _safe_json_error("busy", "an index job is already running"))
            return self._json(202, {"status": job.status, "slot": job.slot, "path": job.path})
        if self.path.split("?", 1)[0] == "/rpc":
            return self._rpc(body)
        return self._send(404, _safe_json_error("not_found", "not found"))

    def _rpc(self, body):
        if not isinstance(body, dict) or body.get("method") != "tools/call" or not isinstance(body.get("params"), dict):
            return self._json(200, {"jsonrpc": "2.0", "id": body.get("id") if isinstance(body, dict) else None,
                                    "error": {"code": -32601, "message": "method not found"}})
        params = body["params"]
        tool = params.get("name")
        if not isinstance(tool, str) or tool not in {"list_projects", "get_graph_schema"}:
            return self._json(200, {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601, "message": "tool not found"}})
        adapter = self._adapter()
        try:
            if tool == "list_projects":
                result = {"projects": [build_project_payload(adapter, self.project_name)]}
            else:
                result = build_schema_payload(adapter)
            text = json.dumps(result, separators=(",", ":"))
        finally:
            adapter.conn.close()
        return self._json(200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {"content": [{"type": "text", "text": text}]}})


def make_handler(adapter_factory, wiki_dir: Path | None = None, *, assets=None, index_manager=None,
                 project_name: str = "", project_root: Path | None = None):
    if not callable(adapter_factory):
        adapter = adapter_factory
        adapter_factory = lambda: type(adapter)(adapter.db_path)
    if assets is None:
        assets = files("kg.viz").joinpath("assets")
    class Handler(_Handler):
        pass
    Handler.adapter_factory = adapter_factory
    Handler.wiki_dir = wiki_dir
    Handler.assets = assets
    Handler.index_manager = index_manager
    Handler.project_name = project_name
    Handler.project_root = project_root
    return Handler


def serve(adapter: StorageAdapter, port: int = 9749, *, wiki_dir: Path | None = None, open_browser: bool = False) -> None:
    from kg.cli.index_cli import index_project
    root = Path(adapter.db_path).resolve().parent
    if root.name == ".kg":
        root = root.parent
    manager = IndexManager(index_project)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(adapter, wiki_dir, index_manager=manager,
        project_name=root.name, project_root=root))
    print(f"kg viz → http://127.0.0.1:{port}/")
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
