"""Pure handler functions for kg MCP tools/resources.

These functions are transport-agnostic: they take JSON-safe primitives
(``project_dir``, simple args) and return JSON-safe dicts/strings. They never
touch the network, never raise tracebacks out, and never leak filesystem paths.
``server.py`` registers them; ``test_handlers.py`` drives them directly with a
FakeEmbedder and a temp project.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from kg.config import Config
from kg.dedup import Deduper
from kg.deepsearch import build_deep_wiki
from kg.dream import dream_candidates
from kg.embed import Embedder, FakeEmbedder, make_embedder
from kg.gate import Gate
from kg.ontology import (
    ALLOWED_NODE_TYPES,
    ALLOWED_SEMANTIC_EDGE_TYPES,
    STRUCTURAL_EDGE_TYPES,
    Edge,
    Node,
)
from kg.pack import pack
from kg.paths import KgPaths
from kg.resolve import Resolver
from kg.search import _VALID_MODES, hybrid_search
from kg.storage.base import Subgraph
from kg.storage.sqlite import SQLiteAdapter
from kg.traverse import expand

log = logging.getLogger(__name__)

# ---- Hard transport/protocol bounds --------------------------------------
# These mirror the brief: bounded k/hops/bytes; reject before we touch the DB.
MAX_K = 100
MAX_HOPS = 5
MAX_HOPS_EXPAND = 5
MAX_RESULT_BYTES = 256_000          # cap any returned JSON payload
MAX_RESOURCE_BYTES = 256_000        # cap resource reads
MAX_PACK_BUDGET_TOKENS = 32_000
MAX_QUERY_CHARS = 4_000
MAX_SOURCE_CHARS = 1_024

# JSON-pointer-ish flat key list we surface in errors (never the raw path).
_SEARCH_MODES = ("hybrid", "bm25", "semantic", "keyword")
_DREAM_KINDS = (
    "recent-pair", "pending", "expiring", "orphan", "contradict",
)

MAX_DEEP_HOPS = 5                   # preflight: hops>5 impossible
MAX_DEEP_QUERY_CHARS = 1_000        # cap query length for deep_search_memory


class HandlerError(Exception):
    """Raised for any expected, user-facing handler failure.

    ``server.py`` catches this and translates it to a structured JSON-RPC error
    payload with ``code`` + ``message`` (no traceback, no fs paths, no secrets).
    Unexpected errors bubble up to the server's generic 500 handler.
    """

    def __init__(self, code: int, message: str, *, safe_extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.safe_extra = safe_extra or {}


# ---------------------------------------------------------------------------
# Path resolution: project root -> KgPaths. Reject everything risky up-front.
# ---------------------------------------------------------------------------


def resolve_paths(project_dir: Path | str) -> KgPaths:
    """Validate ``project_dir`` and return its :class:`KgPaths`.

    Rules (from preflight):
      * must be an existing directory (no symlink-only root)
      * must contain a real ``.kg`` directory
      * reject ``..`` traversal, relative paths that escape, symlinks that
        escape the project root, arbitrary DB paths supplied by the caller
        (we ALWAYS derive ``<root>/.kg`` ourselves — caller never names the DB).
    """
    try:
        root = Path(project_dir).expanduser().resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise HandlerError(
            -32602, "invalid project_dir: not resolvable",
            safe_extra={"reason": type(exc).__name__},
        ) from exc

    # Reject symlinks: we want a real directory the operator pointed at.
    if not root.is_dir():
        raise HandlerError(-32602, "invalid project_dir: not a directory")

    kg_dir = root / ".kg"
    if not kg_dir.is_dir():
        raise HandlerError(
            -32602, "project not initialized: missing .kg/",
        )
    # Symlink-traversal guard: refuse if .kg itself is a symlink (could point
    # outside the resolved project root), and refuse if its resolved parent
    # isn't the resolved root (e.g. nested-worktree symlink tricks). The
    # earlier form compared kg_dir.resolve() to itself and was a no-op.
    try:
        if kg_dir.is_symlink():
            raise HandlerError(
                -32602, ".kg is a symlink — refusing to follow",
            )
        resolved_kg = kg_dir.resolve(strict=True)
        if resolved_kg.parent != root:
            raise HandlerError(
                -32602, ".kg resolves outside project root",
            )
    except OSError as exc:
        raise HandlerError(
            -32602, "invalid project_dir: path check failed",
            safe_extra={"reason": type(exc).__name__},
        ) from exc

    return KgPaths.for_root(kg_dir)


# ---------------------------------------------------------------------------
# Embedder injection: tests pass FakeEmbedder; production resolves via Config.
# ---------------------------------------------------------------------------


def _embedder_for(cfg: Config, embedder: Embedder | None) -> Embedder:
    return embedder if embedder is not None else make_embedder(cfg)


def _build_gate(paths: KgPaths, cfg: Config, embedder: Embedder) -> Gate:
    adapter = SQLiteAdapter(paths.kg_db)
    return Gate(
        adapter,
        Resolver(adapter, embedder, cfg.thresholds),
        Deduper(adapter, embedder, cfg.thresholds),
        embedder,
        cfg,
        cfg.project.user_id,
    )


def _adapter(paths: KgPaths) -> SQLiteAdapter:
    return SQLiteAdapter(paths.kg_db)


def _bound_string(value: Any, *, field: str, max_chars: int) -> str:
    if not isinstance(value, str):
        raise HandlerError(
            -32602, f"invalid {field}: expected string",
        )
    if not value or len(value) > max_chars:
        raise HandlerError(
            -32602,
            f"invalid {field}: empty or exceeds {max_chars} chars",
        )
    return value


def _bound_int(
    value: Any, *, field: str, lo: int, hi: int, default: int | None = None,
) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise HandlerError(-32602, f"invalid {field}: expected integer")
    if not (lo <= value <= hi):
        raise HandlerError(
            -32602,
            f"invalid {field}: must be in [{lo}, {hi}]",
        )
    return value


def _truncate_payload(payload: Any, *, limit: int = MAX_RESULT_BYTES) -> Any:
    """Best-effort byte cap on JSON-serializable payloads.

    Returns the payload untouched if it fits; otherwise returns a small
    structured truncated marker. Callers should still cap items before
    serializing, but this is the safety net.
    """
    rendered = json.dumps(payload, ensure_ascii=False, default=str)
    if len(rendered.encode("utf-8")) <= limit:
        return payload
    return {
        "truncated": True,
        "reason": "result exceeded protocol byte cap",
        "items_before_truncation": (
            len(payload) if isinstance(payload, list) else None
        ),
    }


def _node_safe(node: Node) -> dict:
    """Return a JSON-safe view of a node — no embedding, no internal paths."""
    d = node.model_dump(mode="json")
    d.pop("embedding", None)
    return d


def _edge_safe(edge: Edge) -> dict:
    return edge.model_dump(mode="json")


# ---------------------------------------------------------------------------
# READ TOOLS
# ---------------------------------------------------------------------------


def search_memory(
    project_dir: Path | str,
    *,
    query: str,
    mode: str = "hybrid",
    k: int = 10,
    type_filter: str | None = None,
    embedder: Embedder | None = None,
) -> dict:
    """RRF hybrid search returning ranked node summaries."""
    paths = resolve_paths(project_dir)
    cfg = Config.from_path(paths.config)
    q = _bound_string(query, field="query", max_chars=MAX_QUERY_CHARS)
    if mode not in _SEARCH_MODES:
        raise HandlerError(
            -32602,
            f"invalid mode: expected one of {sorted(_SEARCH_MODES)}",
        )
    k_b = _bound_int(k, field="k", lo=1, hi=MAX_K, default=10)
    if type_filter is not None and type_filter not in ALLOWED_NODE_TYPES:
        raise HandlerError(
            -32602, "invalid type_filter: unknown node type",
            safe_extra={"allowed": sorted(ALLOWED_NODE_TYPES)},
        )

    adapter = _adapter(paths)
    emb = _embedder_for(cfg, embedder)
    ranked = hybrid_search(
        adapter, emb, q, mode=mode, k=k_b,
        type_filter=type_filter, config=cfg,
    )
    items = []
    for node_id, score in ranked:
        node = adapter.get(node_id)
        if node is None:
            continue
        items.append({
            "node_id": node_id,
            "score": round(float(score), 4),
            "type": node.type,
            "name": node.name,
            "summary": node.summary or "",
        })
    return _truncate_payload({"results": items})


def expand_memory(
    project_dir: Path | str,
    *,
    seed_ids: list[str],
    hops: int = 2,
    direction: str = "both",
    edge_types: list[str] | None = None,
    embedder: Embedder | None = None,
) -> dict:
    """BFS-expand from seed node ids, returning a bounded Subgraph."""
    paths = resolve_paths(project_dir)
    cfg = Config.from_path(paths.config)
    if not isinstance(seed_ids, list) or not seed_ids:
        raise HandlerError(-32602, "invalid seed_ids: expected non-empty list")
    if len(seed_ids) > MAX_K:
        raise HandlerError(
            -32602, f"invalid seed_ids: exceeds {MAX_K} entries",
        )
    for sid in seed_ids:
        if not isinstance(sid, str) or not sid:
            raise HandlerError(-32602, "invalid seed_ids: entries must be non-empty strings")
    hops_b = _bound_int(hops, field="hops", lo=0, hi=MAX_HOPS_EXPAND, default=2)
    if direction not in ("both", "outbound", "inbound"):
        raise HandlerError(
            -32602, "invalid direction: expected both|outbound|inbound",
        )
    valid_edge_types = ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES
    if edge_types is not None:
        if not isinstance(edge_types, list) or len(edge_types) > 50:
            raise HandlerError(
                -32602, "invalid edge_types: expected list (<=50)",
            )
        for et in edge_types:
            if et not in valid_edge_types:
                raise HandlerError(
                    -32602, "invalid edge_types: unknown edge type",
                )

    adapter = _adapter(paths)
    cap = min(cfg.query.subgraph_cap, MAX_K * 3)
    sg = expand(
        adapter, seed_ids, hops=hops_b, direction=direction,
        edge_types=edge_types, cap=cap,
    )
    payload = {
        "nodes": [_node_safe(n) for n in sg.nodes],
        "edges": [_edge_safe(e) for e in sg.edges],
        "truncated_count": max(0, len(sg.nodes) - cap),
    }
    return _truncate_payload(payload)


def pack_context(
    project_dir: Path | str,
    *,
    seed_ids: list[str],
    hops: int = 2,
    budget_tokens: int | None = None,
    embedder: Embedder | None = None,
) -> dict:
    """Expand + render the ranked context pack for an agent prompt."""
    paths = resolve_paths(project_dir)
    cfg = Config.from_path(paths.config)
    if not isinstance(seed_ids, list) or not seed_ids:
        raise HandlerError(-32602, "invalid seed_ids: expected non-empty list")
    if len(seed_ids) > MAX_K:
        raise HandlerError(-32602, f"invalid seed_ids: exceeds {MAX_K} entries")
    hops_b = _bound_int(hops, field="hops", lo=0, hi=MAX_HOPS, default=2)
    default_budget = cfg.query.pack_budget_tokens
    if budget_tokens is None:
        budget_tokens = default_budget
    budget_b = _bound_int(
        budget_tokens, field="budget_tokens",
        lo=64, hi=MAX_PACK_BUDGET_TOKENS, default=default_budget,
    )

    adapter = _adapter(paths)
    sg = expand(
        adapter, seed_ids, hops=hops_b,
        cap=min(cfg.query.subgraph_cap, MAX_K * 3),
    )
    rendered = pack(
        sg,
        seeds={sid: 1.0 for sid in seed_ids},
        rrf_scores=None,
        budget_tokens=budget_b,
    )
    return {"markdown": rendered, "tokens_budget": budget_b}


def dream_candidates_tool(
    project_dir: Path | str,
    *,
    since: str | None = None,
    kind: str | None = None,
) -> dict:
    """Surface gray-zone / expiring / orphan / contradict candidates."""
    paths = resolve_paths(project_dir)
    if since is not None:
        _bound_string(since, field="since", max_chars=64)
    if kind is not None and kind not in _DREAM_KINDS:
        raise HandlerError(
            -32602,
            f"invalid kind: expected one of {list(_DREAM_KINDS)}",
        )
    adapter = _adapter(paths)
    candidates = dream_candidates(adapter, since=since)
    if kind:
        candidates = [c for c in candidates if c.reason == kind]
    candidates.sort(
        key=lambda c: (c.reason, -c.score, tuple(c.node_ids), c.edge_id or "")
    )
    items = [
        {
            "reason": c.reason,
            "node_ids": list(c.node_ids),
            "edge_id": c.edge_id,
            "score": round(float(c.score), 4),
            "detail": c.detail,
            "node_type": c.node_type,
        }
        for c in candidates
    ]
    return _truncate_payload({"candidates": items})


def deep_search_memory(
    project_dir: Path | str,
    *,
    query: str,
    hops: int = 3,
    authorized: bool = False,
    embedder: Embedder | None = None,
) -> dict:
    """Materialize a bounded deep-search wiki. Requires write authorization."""
    _authorize(authorized, "deep_search_memory")
    paths = resolve_paths(project_dir)
    cfg = Config.from_path(paths.config)
    q = _bound_string(query, field="query", max_chars=MAX_DEEP_QUERY_CHARS)
    h = _bound_int(hops, field="hops", lo=1, hi=MAX_DEEP_HOPS, default=3)
    report = build_deep_wiki(
        _adapter(paths), _embedder_for(cfg, embedder), q,
        hops=h, wiki_dir=paths.wiki, config=cfg,
    )
    return _truncate_payload({
        "slug": report.slug,
        "pages": report.pages,
        "cached": report.cached,
        "node_count": report.node_count,
        "max_updated_at": report.max_updated_at,
    })


# ---------------------------------------------------------------------------
# RESOURCES (read-only, path-contained)
# ---------------------------------------------------------------------------


def read_wiki_index(project_dir: Path | str) -> str:
    """Read ``<root>/.kg/wiki/index.md`` (bounded bytes)."""
    paths = resolve_paths(project_dir)
    index = paths.wiki_index
    if not index.is_file():
        return ""
    return _read_text_capped(index, MAX_RESOURCE_BYTES)


def read_ontology(project_dir: Path | str) -> str:
    """Read ``<root>/.kg/ontology.json`` (bounded bytes)."""
    paths = resolve_paths(project_dir)
    ontology = paths.ontology
    if not ontology.is_file():
        return ""
    return _read_text_capped(ontology, MAX_RESOURCE_BYTES)


def _read_text_capped(path: Path, limit: int) -> str:
    # Path containment is guaranteed by ``resolve_paths``: we only ever read
    # from the resolved ``.kg`` subtree. Bounds-checked bytes prevent a
    # runaway file from blowing the protocol frame.
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HandlerError(
            -32602, "resource unreadable",
            safe_extra={"reason": type(exc).__name__},
        ) from exc
    if len(raw) > limit:
        raw = raw[:limit]
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# WRITE TOOLS — every one requires explicit server-side authorization.
# ---------------------------------------------------------------------------


def _authorize(authorized: bool, tool: str) -> None:
    """Gate every write tool. Default denied; structured error."""
    if authorized is not True:
        raise HandlerError(
            -32601,
            f"tool '{tool}' not authorized",
            safe_extra={
                "reason": "write tools require explicit server authorization",
                "tool": tool,
            },
        )


def _validate_node_payloads(nodes: Any) -> list[dict]:
    if not isinstance(nodes, list):
        raise HandlerError(-32602, "invalid nodes: expected list")
    if len(nodes) > MAX_K:
        raise HandlerError(-32602, f"invalid nodes: exceeds {MAX_K}")
    out: list[dict] = []
    for idx, n in enumerate(nodes):
        if not isinstance(n, dict):
            raise HandlerError(
                -32602, f"invalid nodes[{idx}]: expected object",
            )
        if n.get("type") not in ALLOWED_NODE_TYPES:
            raise HandlerError(
                -32602,
                f"invalid nodes[{idx}].type: unknown node type",
            )
        name = n.get("name")
        if not isinstance(name, str) or not name or len(name) > 256:
            raise HandlerError(
                -32602, f"invalid nodes[{idx}].name: must be 1..256 chars",
            )
        # Rebuild with only known-safe keys; callers can't smuggle id/status.
        clean = {
            "type": n["type"],
            "name": n["name"],
            "subtype": n.get("subtype") if isinstance(n.get("subtype"), str) else None,
            "aliases": [
                a for a in n.get("aliases", [])
                if isinstance(a, str) and len(a) <= 256
            ] if isinstance(n.get("aliases"), list) else [],
            "summary": n.get("summary") if isinstance(n.get("summary"), str) else None,
            "attributes": {
                k: v for k, v in (n.get("attributes") or {}).items()
                if isinstance(k, str) and len(k) <= 64
            } if isinstance(n.get("attributes"), dict) else {},
            "valid_from": n.get("valid_from") if isinstance(n.get("valid_from"), str) else None,
            "valid_until": n.get("valid_until") if isinstance(n.get("valid_until"), str) else None,
        }
        out.append(clean)
    return out


def _validate_edge_payloads(edges: Any) -> list[dict]:
    valid_sem = ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES
    if not isinstance(edges, list):
        raise HandlerError(-32602, "invalid edges: expected list")
    if len(edges) > MAX_K:
        raise HandlerError(-32602, f"invalid edges: exceeds {MAX_K}")
    out: list[dict] = []
    for idx, e in enumerate(edges):
        if not isinstance(e, dict):
            raise HandlerError(-32602, f"invalid edges[{idx}]: expected object")
        if e.get("semantic_type") not in valid_sem:
            raise HandlerError(
                -32602, f"invalid edges[{idx}].semantic_type: unknown edge type",
            )
        for key in ("source_name", "target_name"):
            v = e.get(key)
            if not isinstance(v, str) or not v or len(v) > 256:
                raise HandlerError(
                    -32602, f"invalid edges[{idx}].{key}: must be 1..256 chars",
                )
        conf = e.get("confidence", 0.0)
        if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
            raise HandlerError(
                -32602, f"invalid edges[{idx}].confidence: must be 0..1",
            )
        out.append({
            "semantic_type": e["semantic_type"],
            "source_name": e["source_name"],
            "target_name": e["target_name"],
            "confidence": float(conf),
            "summary": e.get("summary") if isinstance(e.get("summary"), str) else None,
        })
    return out


def save_pole(
    project_dir: Path | str,
    *,
    nodes: list[dict],
    edges: list[dict],
    source: str,
    facts: list[dict] | None = None,
    preferences: list[dict] | None = None,
    authorized: bool = False,
    embedder: Embedder | None = None,
) -> dict:
    """Persist a Pole (nodes+edges) batch through the Gate.

    Requires ``authorized=True`` — server config sets this, callers cannot.
    """
    _authorize(authorized, "save_pole")
    paths = resolve_paths(project_dir)
    cfg = Config.from_path(paths.config)
    src = _bound_string(source, field="source", max_chars=MAX_SOURCE_CHARS)
    clean_nodes = _validate_node_payloads(nodes)
    clean_edges = _validate_edge_payloads(edges)
    clean_facts = _validate_facts(facts) if facts else None
    clean_prefs = _validate_preferences(preferences) if preferences else None

    emb = _embedder_for(cfg, embedder)
    gate = _build_gate(paths, cfg, emb)
    try:
        report = gate.normalize(
            clean_nodes, clean_edges, src,
            facts=clean_facts, preferences=clean_prefs,
        )
    except ValueError as exc:
        # Gate rejected (unknown type at write time, bad shape, etc.).
        raise HandlerError(-32602, "save_pole rejected by gate") from exc
    return {
        "edges_upserted": report.edges_upserted,
        "new_same_as": report.new_same_as,
        "decisions": [
            {
                "action": d.action, "type": d.type, "name": d.name,
                "score": round(float(d.score), 4), "via": d.via,
            }
            for d in report.decisions
        ],
        "dropped_edges": [
            {
                "source_name": d["source_name"], "target_name": d["target_name"],
                "semantic_type": d["semantic_type"], "reason": d["reason"],
            }
            for d in report.dropped_edges
        ],
    }


def _validate_facts(facts: Any) -> list[dict]:
    if not isinstance(facts, list) or len(facts) > MAX_K:
        raise HandlerError(-32602, "invalid facts: expected list (<=100)")
    out = []
    for idx, f in enumerate(facts):
        if not isinstance(f, dict):
            raise HandlerError(-32602, f"invalid facts[{idx}]: expected object")
        for key in ("subject", "predicate", "object"):
            v = f.get(key)
            if not isinstance(v, str) or not v or len(v) > 256:
                raise HandlerError(
                    -32602, f"invalid facts[{idx}].{key}: must be 1..256 chars",
                )
        out.append({
            "subject": f["subject"], "predicate": f["predicate"],
            "object": f["object"],
            "summary": f.get("summary") if isinstance(f.get("summary"), str) else None,
        })
    return out


def _validate_preferences(prefs: Any) -> list[dict]:
    if not isinstance(prefs, list) or len(prefs) > MAX_K:
        raise HandlerError(-32602, "invalid preferences: expected list (<=100)")
    out = []
    for idx, p in enumerate(prefs):
        if not isinstance(p, dict):
            raise HandlerError(-32602, f"invalid preferences[{idx}]: expected object")
        name = p.get("name") or p.get("subject")
        if not isinstance(name, str) or not name or len(name) > 256:
            raise HandlerError(
                -32602, f"invalid preferences[{idx}]: name or subject required (1..256)",
            )
        out.append({
            "name": name,
            "summary": p.get("summary") if isinstance(p.get("summary"), str) else None,
            "attributes": {
                k: v for k, v in (p.get("attributes") or {}).items()
                if isinstance(k, str) and len(k) <= 64
            } if isinstance(p.get("attributes"), dict) else dict(p),
            "valid_from": p.get("valid_from") if isinstance(p.get("valid_from"), str) else None,
            "valid_until": p.get("valid_until") if isinstance(p.get("valid_until"), str) else None,
        })
    return out


def review_confirm(
    project_dir: Path | str,
    *,
    edge_id: str,
    winner_id: str,
    reason: str | None = None,
    authorized: bool = False,
    embedder: Embedder | None = None,
) -> dict:
    """Confirm a pending same_as review: merge loser into winner. Audit preserved.

    Routes through Gate so M2's preimage capture + audit record semantics are
    preserved verbatim. The audit ``log.md`` append is left to the CLI; MCP
    surfaces the structured AuditRecord back to the caller instead.
    """
    _authorize(authorized, "review_confirm")
    paths = resolve_paths(project_dir)
    cfg = Config.from_path(paths.config)
    eid = _bound_string(edge_id, field="edge_id", max_chars=256)
    wid = _bound_string(winner_id, field="winner_id", max_chars=256)
    reason_safe = (
        _bound_string(reason, field="reason", max_chars=512)
        if reason is not None else None
    )

    emb = _embedder_for(cfg, embedder)
    adapter = _adapter(paths)
    gate = _build_gate(paths, cfg, emb)
    # winner must be one of the edge endpoints (M2 invariant).
    row = adapter.conn.execute(
        "SELECT source, target FROM edges WHERE id=?", (eid,)
    ).fetchone()
    if row is None:
        raise HandlerError(-32602, "unknown edge_id")
    if wid not in {row["source"], row["target"]}:
        raise HandlerError(
            -32602, "winner_id must be one of the edge endpoints",
        )
    loser_id = row["target"] if wid == row["source"] else row["source"]
    try:
        audit = gate.review_merge(eid, wid, loser_id)
    except ValueError as exc:
        raise HandlerError(-32602, "review_confirm rejected by gate") from exc
    if reason_safe is not None:
        _append_review_audit_reason(paths, eid, reason_safe, audit.action)
    return {
        "action": audit.action,
        "edge_id": audit.review_edge_id,
        "winner_id": audit.winner_id,
        "loser_id": audit.loser_id,
        "reason": reason_safe,
        "timestamp": audit.timestamp,
        # NOTE: audit body (winner_before/loser_before/edges_before) is NOT
        # surfaced by default — large + may carry PII. CLI writes full body
        # to .kg/wiki/log.md; MCP returns only the safe envelope. This is
        # the audit-path concern flagged in the brief.
    }


def review_reject(
    project_dir: Path | str,
    *,
    edge_id: str,
    reason: str | None = None,
    authorized: bool = False,
    embedder: Embedder | None = None,
) -> dict:
    """Reject a pending same_as review: mark edge status=rejected."""
    _authorize(authorized, "review_reject")
    paths = resolve_paths(project_dir)
    cfg = Config.from_path(paths.config)
    eid = _bound_string(edge_id, field="edge_id", max_chars=256)
    reason_safe = (
        _bound_string(reason, field="reason", max_chars=512)
        if reason is not None else None
    )

    emb = _embedder_for(cfg, embedder)
    gate = _build_gate(paths, cfg, emb)
    try:
        audit = gate.reject_review(eid)
    except ValueError as exc:
        raise HandlerError(-32602, "review_reject rejected by gate") from exc
    if reason_safe is not None:
        _append_review_audit_reason(paths, eid, reason_safe, audit.action)
    return {
        "action": audit.action,
        "edge_id": audit.review_edge_id,
        "reason": reason_safe,
        "timestamp": audit.timestamp,
    }


def merge_nodes(
    project_dir: Path | str,
    *,
    winner_id: str,
    loser_id: str,
    reason: str | None = None,
    authorized: bool = False,
    embedder: Embedder | None = None,
) -> dict:
    """Manual merge (no review edge). Routes through Gate."""
    _authorize(authorized, "merge_nodes")
    paths = resolve_paths(project_dir)
    cfg = Config.from_path(paths.config)
    wid = _bound_string(winner_id, field="winner_id", max_chars=256)
    lid = _bound_string(loser_id, field="loser_id", max_chars=256)
    reason_safe = (
        _bound_string(reason, field="reason", max_chars=512)
        if reason is not None else None
    )

    emb = _embedder_for(cfg, embedder)
    gate = _build_gate(paths, cfg, emb)
    try:
        audit = gate.merge(wid, lid, reason=reason_safe)
    except ValueError as exc:
        raise HandlerError(-32602, "merge_nodes rejected by gate") from exc
    return {
        "action": audit.action,
        "winner_id": audit.winner_id,
        "loser_id": audit.loser_id,
        "reason": reason_safe,
        "timestamp": audit.timestamp,
    }


def _append_review_audit_reason(
    paths: KgPaths, edge_id: str, reason: str, action: str,
) -> None:
    """Append MCP-supplied reason to .kg/wiki/log.md alongside Gate's audit.

    Gate.review_merge/reject_review/merge do not accept ``reason``; the CLI
    layer has always appended it after the fact. Mirror that for MCP so the
    audit trail records why a human confirmed/rejected. Best-effort: an OS
    failure here is logged but does not fail the write (the DB commit is
    already durable).
    """
    import logging as _logging
    record = {
        "timestamp": _now_iso(),
        "action": f"{action}:reason",
        "edge_id": edge_id,
        "reason": reason,
        "source": "mcp",
    }
    log = _logging.getLogger("kg.mcp.audit")
    try:
        with (paths.wiki / "log.md").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("audit reason append failed for %s: %s", edge_id, exc)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tool/resource registry — input schemas with additionalProperties:false.
# server.py consumes this single source of truth.
# ---------------------------------------------------------------------------

# A "str" type alias for JSON Schema; we use the literal "string" throughout.
_STR = "string"
_INT = "integer"
_ARR = "array"
_OBJ = "object"


def _t_string() -> dict:
    return {"type": _STR}


def _t_int(lo: int, hi: int) -> dict:
    return {"type": _INT, "minimum": lo, "maximum": hi}


def _enum(values: list[str]) -> dict:
    return {"type": _STR, "enum": values}


READ_TOOLS: dict[str, dict] = {
    "search_memory": {
        "description": (
            "RRF hybrid search over the project memory graph. "
            "Returns ranked node summaries (id, score, type, name, summary)."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": _STR, "minLength": 1, "maxLength": MAX_QUERY_CHARS},
                "mode": _enum(list(_VALID_MODES)),
                "k": _t_int(1, MAX_K),
                "type_filter": _enum(sorted(ALLOWED_NODE_TYPES)),
            },
        },
    },
    "expand_memory": {
        "description": (
            "Breadth-first expand from seed node ids. Returns a bounded "
            "Subgraph (nodes + edges)."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "required": ["seed_ids"],
            "properties": {
                "seed_ids": {
                    "type": _ARR, "minItems": 1, "maxItems": MAX_K,
                    "items": {"type": _STR, "minLength": 1, "maxLength": 256},
                },
                "hops": _t_int(0, MAX_HOPS_EXPAND),
                "direction": _enum(["both", "outbound", "inbound"]),
                "edge_types": {
                    "type": _ARR, "maxItems": 50,
                    "items": {"type": _STR, "minLength": 1, "maxLength": 64},
                },
            },
        },
    },
    "pack_context": {
        "description": (
            "Expand + render the ranked context pack for an agent prompt. "
            "Returns markdown + budget_tokens."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "required": ["seed_ids"],
            "properties": {
                "seed_ids": {
                    "type": _ARR, "minItems": 1, "maxItems": MAX_K,
                    "items": {"type": _STR, "minLength": 1, "maxLength": 256},
                },
                "hops": _t_int(0, MAX_HOPS),
                "budget_tokens": _t_int(64, MAX_PACK_BUDGET_TOKENS),
            },
        },
    },
    "dream_candidates": {
        "description": (
            "Surface gray-zone / pending / expiring / orphan / contradict "
            "candidates for human review."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "properties": {
                "since": {"type": _STR, "maxLength": 64},
                "kind": _enum(list(_DREAM_KINDS)),
            },
        },
    },
}

WRITE_TOOLS: dict[str, dict] = {
    "save_pole": {
        "description": (
            "Persist a Pole (nodes+edges [+facts/preferences]) through the "
            "Gate. REQUIRES server-side write authorization."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "required": ["nodes", "edges", "source"],
            "properties": {
                "nodes": {
                    "type": _ARR, "maxItems": MAX_K,
                    "items": {
                        "type": _OBJ, "additionalProperties": False,
                        "required": ["type", "name"],
                        "properties": {
                            "type": _enum(sorted(ALLOWED_NODE_TYPES)),
                            "name": {"type": _STR, "minLength": 1, "maxLength": 256},
                            "subtype": {"type": _STR, "maxLength": 128},
                            "aliases": {
                                "type": _ARR, "maxItems": 50,
                                "items": {"type": _STR, "maxLength": 256},
                            },
                            "summary": {"type": _STR, "maxLength": 8_000},
                            "attributes": {"type": _OBJ, "maxProperties": 100},
                            "valid_from": {"type": _STR, "maxLength": 64},
                            "valid_until": {"type": _STR, "maxLength": 64},
                        },
                    },
                },
                "edges": {
                    "type": _ARR, "maxItems": MAX_K,
                    "items": {
                        "type": _OBJ, "additionalProperties": False,
                        "required": ["source_name", "target_name", "semantic_type"],
                        "properties": {
                            "source_name": {"type": _STR, "minLength": 1, "maxLength": 256},
                            "target_name": {"type": _STR, "minLength": 1, "maxLength": 256},
                            "semantic_type": _enum(sorted(ALLOWED_SEMANTIC_EDGE_TYPES | STRUCTURAL_EDGE_TYPES)),
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "summary": {"type": _STR, "maxLength": 8_000},
                        },
                    },
                },
                "source": {"type": _STR, "minLength": 1, "maxLength": MAX_SOURCE_CHARS},
                "facts": {
                    "type": _ARR, "maxItems": MAX_K,
                    "items": {
                        "type": _OBJ, "additionalProperties": False,
                        "required": ["subject", "predicate", "object"],
                        "properties": {
                            "subject": {"type": _STR, "minLength": 1, "maxLength": 256},
                            "predicate": {"type": _STR, "minLength": 1, "maxLength": 256},
                            "object": {"type": _STR, "minLength": 1, "maxLength": 256},
                            "summary": {"type": _STR, "maxLength": 8_000},
                        },
                    },
                },
                "preferences": {
                    "type": _ARR, "maxItems": MAX_K,
                    "items": {
                        "type": _OBJ, "additionalProperties": False,
                        "anyOf": [{"required": ["name"]}, {"required": ["subject"]}],
                        "properties": {
                            "name": {"type": _STR, "minLength": 1, "maxLength": 256},
                            "subject": {"type": _STR, "minLength": 1, "maxLength": 256},
                            "summary": {"type": _STR, "maxLength": 8_000},
                            "attributes": {"type": _OBJ, "maxProperties": 100},
                            "valid_from": {"type": _STR, "maxLength": 64},
                            "valid_until": {"type": _STR, "maxLength": 64},
                        },
                    },
                },
            },
        },
    },
    "review_confirm": {
        "description": (
            "Confirm a pending same_as review; merge loser into winner "
            "(winner must be an edge endpoint). REQUIRES write authorization."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "required": ["edge_id", "winner_id"],
            "properties": {
                "edge_id": {"type": _STR, "minLength": 1, "maxLength": 256},
                "winner_id": {"type": _STR, "minLength": 1, "maxLength": 256},
                "reason": {"type": _STR, "maxLength": 512},
            },
        },
    },
    "review_reject": {
        "description": (
            "Reject a pending same_as review. REQUIRES write authorization."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "required": ["edge_id"],
            "properties": {
                "edge_id": {"type": _STR, "minLength": 1, "maxLength": 256},
                "reason": {"type": _STR, "maxLength": 512},
            },
        },
    },
    "merge_nodes": {
        "description": (
            "Manually merge two nodes (winner absorbs loser). REQUIRES write "
            "authorization."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "required": ["winner_id", "loser_id"],
            "properties": {
                "winner_id": {"type": _STR, "minLength": 1, "maxLength": 256},
                "loser_id": {"type": _STR, "minLength": 1, "maxLength": 256},
                "reason": {"type": _STR, "maxLength": 512},
            },
        },
    },
    "deep_search_memory": {
        "description": (
            "Build a scoped deep-search wiki: hybrid_search -> expand -> "
            "materialize pages under wiki/deep/<slug>/. REQUIRES write "
            "authorization (materializes files)."
        ),
        "inputSchema": {
            "type": _OBJ,
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": _STR, "minLength": 1, "maxLength": MAX_DEEP_QUERY_CHARS},
                "hops": _t_int(1, MAX_DEEP_HOPS),
            },
        },
    },
}

ALL_TOOLS: dict[str, dict] = {**READ_TOOLS, **WRITE_TOOLS}


RESOURCES: list[dict] = [
    {
        "name": "ontology",
        "uri": "kg://ontology.json",
        "description": "Project ontology schema (node + edge types).",
        "mimeType": "application/json",
    },
    {
        "name": "wiki_index",
        "uri": "kg://wiki/index.md",
        "description": "Project memory wiki index (markdown).",
        "mimeType": "text/markdown",
    },
]


__all__ = [
    "HandlerError",
    "READ_TOOLS", "WRITE_TOOLS", "ALL_TOOLS", "RESOURCES",
    "MAX_K", "MAX_HOPS", "MAX_RESULT_BYTES", "MAX_RESOURCE_BYTES",
    # Path + auth helpers
    "resolve_paths",
    # Read tools
    "search_memory", "expand_memory", "pack_context", "dream_candidates_tool",
    # Resources
    "read_wiki_index", "read_ontology",
    # Write tools
    "save_pole", "review_confirm", "review_reject", "merge_nodes", "deep_search_memory",
    # Test affordance
    "FakeEmbedder",
]
