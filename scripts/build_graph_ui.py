"""Build the packaged CBM graph UI.

Consumes a static snapshot source JSON (retained ``{nodes,edges}`` shape),
runs the same normalization + layout serialization pipeline as the live
``/api/layout`` payload, builds the npm project, and atomically replaces
``src/kg/viz/assets`` with the self-contained static bundle.

Only stdlib. Node is invoked at build time; runtime needs no Node.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIZ_UI = ROOT / "ui" / "graph-ui"
DEFAULT_SOURCE = VIZ_UI / "snapshots" / "demo-source.json"
DEFAULT_DIST = VIZ_UI / "dist"
DEFAULT_OUTPUT = ROOT / "src" / "kg" / "viz" / "assets"

# Static build ceiling includes the 10k/15k Pages stress demo.
_MAX_NODES = 12_000
_MAX_EDGES = 18_000

_IMPORT_RE = re.compile(
    r"""(?:src|href)=(["']?)([^"' >\t]+)\1|url\(\s*(["']?)([^)"'\s]+)\3""",
    re.IGNORECASE,
)


def _validate_source(source: Path) -> dict:
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid snapshot source {source}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list) or not isinstance(raw.get("edges"), list):
        raise SystemExit(f"invalid snapshot source {source}: expected {{nodes,edges}} object")
    if len(raw["nodes"]) > _MAX_NODES:
        raise SystemExit(f"snapshot nodes {len(raw['nodes'])} exceed max {_MAX_NODES}")
    if len(raw["edges"]) > _MAX_EDGES:
        raise SystemExit(f"snapshot edges {len(raw['edges'])} exceed max {_MAX_EDGES}")
    for n in raw["nodes"]:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            raise SystemExit(f"invalid snapshot node entry: {n!r}")
    for e in raw["edges"]:
        if not isinstance(e, dict) or not isinstance(e.get("source"), str) or not isinstance(e.get("target"), str):
            raise SystemExit(f"invalid snapshot edge entry: {e!r}")
    return raw


def _snapshot_payload(raw: dict) -> dict:
    """Normalize the retained snapshot shape through the shared layout pipeline.

    Node ids become kg_ids; degree counts only edges whose endpoints both
    appear in the retained node list (mirrors the live SQL degree query on the
    retained set). Coordinate/color logic stays in kg.viz.layout3d/api.
    """
    from kg.viz.api import _COLOR_BY_TYPE, _DEFAULT_COLOR, _cluster_key, _serialize_layout_payload
    from kg.viz.layout3d import LayoutEdgeInput, LayoutNodeInput

    nodes = raw["nodes"]
    edges = raw["edges"]
    ids = [n["id"] for n in nodes]
    id_set = set(ids)
    deg: dict[str, int] = {nid: 0 for nid in ids}
    for e in edges:
        if e["source"] in id_set and e["target"] in id_set:
            deg[e["source"]] = deg.get(e["source"], 0) + 1
            deg[e["target"]] = deg.get(e["target"], 0) + 1

    meta: dict[str, dict[str, object]] = {}
    layout_nodes: list[LayoutNodeInput] = []
    for n in nodes:
        kg_id = n["id"]
        node_type = str(n.get("type") or "unknown")
        name = str(n.get("name") or kg_id)
        attrs = n.get("attributes")
        raw_path = attrs.get("path") if isinstance(attrs, dict) else n.get("path")
        file_path = raw_path if isinstance(raw_path, str) else None
        meta[kg_id] = {
            "label": node_type,
            "name": name,
            "color": _COLOR_BY_TYPE.get(node_type, _DEFAULT_COLOR),
            "file_path": file_path,
        }
        cluster_data = dict(n)
        if file_path is not None:
            cluster_data["attributes"] = {"path": file_path}
        layout_nodes.append(
            LayoutNodeInput(kg_id, _cluster_key(cluster_data), name, node_type, deg.get(kg_id, 0))
        )
    layout_edges = [LayoutEdgeInput(e["source"], e["target"], str(e.get("type") or "related_to")) for e in edges]
    return _serialize_layout_payload(
        layout_nodes, layout_edges, meta,
        total_nodes=len(nodes),
        truncated_nodes=bool(raw.get("truncated_nodes")),
        truncated_edges=bool(raw.get("truncated_edges")),
    )


def _scan_external_imports(build_dir: Path) -> list[str]:
    """Find absolute asset references in HTML/CSS build outputs.

    Bundled JS may legally embed library-internal URL constants (XML namespace
    URIs, error-doc strings, license comments) that are never fetched; those
    are invisible to the runtime trust boundary. The enforceable boundary is
    what the page actually loads: src/href/url() asset references, which must
    all be relative. Visible attribution links (href= in markup) stay allowed.
    """
    allowed = {"https://github.com/DeusData/codebase-memory-mcp"}
    hits: list[str] = []
    for p in build_dir.rglob("*"):
        if not p.is_file() or p.suffix not in {".html", ".css"}:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _IMPORT_RE.finditer(text):
            ref = m.group(2) if m.group(2) is not None else m.group(4)
            if ref is None or ref.startswith((".", "/", "#", "data:", "blob:")):
                continue
            if ref.startswith(("http://", "https://", "//")):
                if ref in allowed:
                    continue
                hits.append(f"{p.relative_to(build_dir)}: {ref!r}")
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the packaged CBM graph UI")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    raw = _validate_source(source)

    from kg.viz.api import build_capabilities, json_bytes

    snapshot = _snapshot_payload(raw)
    capabilities = build_capabilities(static=True)

    if not args.skip_install:
        subprocess.run(["npm", "ci"], cwd=VIZ_UI, check=True)
    if not args.skip_tests:
        subprocess.run(["npm", "test", "--", "--run"], cwd=VIZ_UI, check=True)
    subprocess.run(["npm", "run", "build"], cwd=VIZ_UI, check=True)

    dist = args.dist.resolve()
    if not (dist / "index.html").is_file():
        raise SystemExit(f"build output missing index.html in {dist}")

    external = _scan_external_imports(dist)
    if external:
        for hit in external[:20]:
            print(f"external URL: {hit}", file=sys.stderr)
        raise SystemExit(f"{len(external)} external URL(s) in build output")

    output = args.output.resolve()
    staging = output.with_name(f".{output.name}.tmp-{__import__('os').getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(dist, staging)
    (staging / "graph-snapshot.json").write_bytes(json_bytes(snapshot))
    (staging / "capabilities.json").write_bytes(json_bytes(capabilities))
    if output.exists():
        shutil.rmtree(output)
    staging.replace(output)
    print(f"packaged {output} ({sum(1 for p in output.rglob('*') if p.is_file())} files)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
