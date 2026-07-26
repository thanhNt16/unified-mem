"""Local-first kg viz server. Binds 127.0.0.1 ONLY — never 0.0.0.0."""
from __future__ import annotations

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

from kg.community import louvain
from kg.storage.base import StorageAdapter

# Cap graph payload to keep browser-side perf bounded.
_MAX_NODES = 2000
_MAX_EDGES = 4000
_MAX_BYTES = 4 * 1024 * 1024  # 4 MiB

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'"
)

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>kg viz</title>
<style>
:root { color-scheme: light dark; }
body { margin: 0; font: 14px/1.4 system-ui, sans-serif; background: #fafafa; color: #222; }
header { padding: 8px 12px; border-bottom: 1px solid #ddd; }
main { display: grid; grid-template-columns: 1fr 320px; height: calc(100vh - 41px); }
#canvas { background: #fff; position: relative; overflow: hidden; }
#side { padding: 12px; border-left: 1px solid #ddd; overflow: auto; background: #f6f6f6; }
h1 { font-size: 16px; margin: 0; }
.node { stroke: #fff; stroke-width: 1.5px; cursor: pointer; }
.edge { stroke: #ccc; stroke-width: 1px; }
.label { font: 10px sans-serif; pointer-events: none; }
small { color: #666; }
</style>
</head>
<body>
<header><h1>kg viz</h1><small id="meta"></small></header>
<main>
<svg id="canvas" width="100%" height="100%"></svg>
<aside id="side"><em>Loading...</em></aside>
</main>
<script src="/app.js"></script>
</body>
</html>
"""

_JS = """
// Minimal self-contained force-directed renderer. No CDN, no network.
const svg = document.getElementById('canvas');
const side = document.getElementById('side');
const W = svg.clientWidth, H = svg.clientHeight;
const NS = 'http://www.w3.org/2000/svg';
const POLE_COLORS = {
  person:'#3b82f6', organization:'#ef4444', location:'#10b981', event:'#f59e0b',
  object:'#8b5cf6', preference:'#ec4899', fact:'#0ea5e9', document:'#6b7280',
  chunk:'#9ca3af', conversation:'#22c55e', session:'#a78bfa'
};
function nodeColor(t){ return POLE_COLORS[t] || '#475569'; }

fetch('/graph.json').then(r => r.json()).then(data => {
  document.getElementById('meta').textContent = data.nodes.length + ' nodes / ' + data.edges.length + ' edges';
  if (!data.nodes.length){ side.innerHTML = '<em>Empty graph.</em>'; return; }
  const nodes = data.nodes.map(n => ({...n, x: Math.random()*W, y: Math.random()*H, vx:0, vy:0}));
  const byId = {}; nodes.forEach(n => byId[n.id] = n);
  const edges = data.edges.filter(e => byId[e.source] && byId[e.target]);
  // simple force layout
  for (let iter=0; iter<300; iter++){
    edges.forEach(e => {
      const a = byId[e.source], b = byId[e.target];
      let dx = b.x-a.x, dy = b.y-a.y; const d = Math.hypot(dx,dy)||1;
      const f = (d-80)*0.01;
      a.vx += f*dx/d; a.vy += f*dy/d; b.vx -= f*dx/d; b.vy -= f*dy/d;
    });
    nodes.forEach(n => {
      n.x += n.vx*0.85; n.y += n.vy*0.85; n.vx*=0.9; n.vy*=0.9;
      n.x = Math.max(20, Math.min(W-20, n.x));
      n.y = Math.max(20, Math.min(H-20, n.y));
    });
  }
  // render
  edges.forEach(e => {
    const ln = document.createElementNS(NS,'line');
    ln.setAttribute('x1',byId[e.source].x); ln.setAttribute('y1',byId[e.source].y);
    ln.setAttribute('x2',byId[e.target].x); ln.setAttribute('y2',byId[e.target].y);
    ln.setAttribute('class','edge'); svg.appendChild(ln);
  });
  nodes.forEach(n => {
    const c = document.createElementNS(NS,'circle');
    c.setAttribute('cx',n.x); c.setAttribute('cy',n.y);
    c.setAttribute('r', 5 + (n.degree||0)*0.5);
    c.setAttribute('fill', nodeColor(n.type));
    c.setAttribute('class','node');
    c.onclick = () => {
      side.innerHTML = '<h3>' + (n.name||n.id) + '</h3>' +
        '<p><b>type:</b> ' + (n.type||'?') + '<br>' +
        '<b>cluster:</b> ' + (n.cluster??-1) + '<br>' +
        '<b>degree:</b> ' + (n.degree||0) + '</p>' +
        '<p>' + (n.summary||'') + '</p>';
    };
    svg.appendChild(c);
    const t = document.createElementNS(NS,'text');
    t.setAttribute('x', n.x+8); t.setAttribute('y', n.y+4);
    t.setAttribute('class','label');
    t.textContent = (n.name||n.id).slice(0,20);
    svg.appendChild(t);
  });
  side.innerHTML = '<em>Click a node.</em>';
}).catch(e => { side.innerHTML = '<b>error:</b> ' + e; });
"""


def _graph_payload(adapter: StorageAdapter) -> dict:
    n_rows = adapter.conn.execute(
        "SELECT id, data, status FROM nodes WHERE status='active' ORDER BY id LIMIT ?",
        (_MAX_NODES + 1,),
    ).fetchall()
    truncated_nodes = len(n_rows) > _MAX_NODES
    n_rows = n_rows[:_MAX_NODES]

    clusters = louvain(adapter)
    active_ids = {r["id"] for r in n_rows}

    # degree counts via active edges only
    deg: dict[str, int] = {}
    e_rows = adapter.conn.execute(
        "SELECT source, target, data FROM edges WHERE status='active' ORDER BY id"
    ).fetchall()
    edges_out: list[dict] = []
    truncated_edges = False
    for r in e_rows:
        s, t = r["source"], r["target"]
        if s not in active_ids or t not in active_ids:
            continue
        deg[s] = deg.get(s, 0) + 1
        deg[t] = deg.get(t, 0) + 1
        try:
            data = json.loads(r["data"])
            sem = data.get("semantic_type", "")
        except (ValueError, TypeError):
            sem = ""
        edges_out.append({"source": s, "target": t, "semantic_type": sem})
        if len(edges_out) >= _MAX_EDGES:
            truncated_edges = True
            break

    def esc(s) -> str:
        return html.escape(str(s or ""))

    nodes_out = []
    for r in n_rows:
        try:
            data = json.loads(r["data"])
        except (ValueError, TypeError):
            data = {}
        nid = r["id"]
        nodes_out.append({
            "id": esc(nid),
            "name": esc(data.get("name") or nid),
            "type": esc(data.get("type") or ""),
            "summary": esc(data.get("summary") or ""),
            "degree": deg.get(nid, 0),
            "cluster": clusters.get(nid, -1),
        })

    return {
        "nodes": nodes_out,
        "edges": edges_out,
        "truncated_nodes": truncated_nodes,
        "truncated_edges": truncated_edges,
    }


def _resolve_wiki_page(wiki_dir: Path, raw_slug: str) -> Path | None:
    """Resolve a slug inside wiki/entities/; reject traversal, symlinks, escape."""
    if not raw_slug or not isinstance(raw_slug, str):
        return None
    if any(ord(c) < 32 for c in raw_slug):
        return None
    if "\\" in raw_slug or "\x00" in raw_slug:
        return None
    # Reject URL scheme, parent segments.
    p = PurePosixPath(raw_slug.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or len(p.parts) != 1:
        return None
    if not p.name.endswith(".md"):
        return None
    entities = (Path(wiki_dir) / "entities").resolve(strict=False)
    target = (entities / p.name).resolve(strict=False)
    try:
        target.relative_to(entities)
    except ValueError:
        return None
    # Symlink escape: target must be inside entities and not a symlink itself.
    if target.is_symlink():
        return None
    if not target.is_file():
        return None
    return target


class _Handler(BaseHTTPRequestHandler):
    db_path: Path
    wiki_dir: Path | None

    def log_message(self, *args):  # silence default stderr logging
        pass

    def _adapter(self):
        # Open a per-request connection: SQLite connections are thread-bound,
        # and ThreadingHTTPServer serves each request on its own thread.
        from kg.storage.sqlite import SQLiteAdapter
        return SQLiteAdapter(self.db_path)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", _CSP)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        # never write body on HEAD; BaseHTTPRequestHandler doesn't auto-suppress
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            self._send(200, _HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._send(200, _JS.encode("utf-8"), "application/javascript; charset=utf-8")
            return
        if path == "/graph.json":
            adapter = self._adapter()
            try:
                payload = _graph_payload(adapter)
            finally:
                adapter.conn.close()
            body = json.dumps(payload).encode("utf-8")
            if len(body) > _MAX_BYTES:
                # Truncate by sending nodes/edges only up to cap (already capped).
                # If still over, send minimal stub.
                stub = {"nodes": payload["nodes"][:500],
                        "edges": payload["edges"][:1000],
                        "truncated_nodes": True, "truncated_edges": True}
                body = json.dumps(stub).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path.startswith("/wiki/") and self.wiki_dir is not None:
            slug = path[len("/wiki/"):]
            resolved = _resolve_wiki_page(self.wiki_dir, slug)
            if resolved is None:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                content = resolved.read_text(encoding="utf-8")
            except OSError:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, content.encode("utf-8"), "text/markdown; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_HEAD(self):  # noqa: N802
        self.do_GET()


def make_handler(adapter: StorageAdapter, wiki_dir: Path | None):
    class H(_Handler):
        pass
    H.db_path = Path(adapter.db_path)  # type: ignore[attr-defined]
    H.wiki_dir = wiki_dir  # type: ignore[attr-defined]
    return H


def serve(adapter: StorageAdapter, port: int = 9749, *,
          wiki_dir: Path | None = None, open_browser: bool = False) -> None:
    """Serve kg viz on 127.0.0.1:port. Never binds 0.0.0.0.

    ``open_browser`` is accepted but ignored — never auto-open. Callers print
    the URL to stdout for the user to click. (S5: no auto-open.)
    """
    del open_browser  # accepted for API symmetry; intentionally unused
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(adapter, wiki_dir))
    print(f"kg viz → http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
