"""MCP stdio server for kg.

Builds an ``mcp.server.Server`` that registers the realized kg tools +
resources defined in :mod:`kg.mcp.handlers`. The handlers are pure functions;
this module only:
  * owns the transport (stdio),
  * translates ``HandlerError`` to structured JSON-RPC error payloads,
  * enforces the default-denied write authorization policy via a single
    server-level capability flag.

Stdout emits JSON-RPC frames ONLY. All logs/diagnostics go to stderr.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from kg import __version__
from kg.mcp import handlers as H

log = logging.getLogger("kg.mcp")

SERVER_NAME = "kg"
SERVER_VERSION = __version__

# Tools that require explicit server-side write authorization. The set is
# derived from the handler registry so it stays in sync.
_WRITE_TOOL_NAMES = frozenset(H.WRITE_TOOLS.keys())

# Resource URI prefix we own. Anything else is rejected as not-found.
_RESOURCE_PREFIX = "kg://"

# Map resource URI -> (handler, mime_type).
_RESOURCE_ROUTES: dict[str, tuple[Any, str]] = {
    "kg://ontology.json": (H.read_ontology, "application/json"),
    "kg://wiki/index.md": (H.read_wiki_index, "text/markdown"),
}


def _to_error_payload(exc: H.HandlerError) -> dict:
    """Translate a HandlerError into a JSON-RPC error content envelope."""
    body = {"code": exc.code, "message": exc.message}
    if exc.safe_extra:
        # Only ever surface the structured safe_extra; never raw args/paths.
        body["data"] = exc.safe_extra
    return body


def build_server(
    project_dir: Path | str,
    *,
    allow_writes: bool = False,
    embedder=None,
) -> Server:
    """Construct the kg MCP server.

    Args:
        project_dir: Project root containing a real ``.kg/`` directory.
        allow_writes: Explicit server capability. When ``False`` (default),
            every write tool returns a structured "not authorized" error.
        embedder: Optional injectable embedder (tests use FakeEmbedder).
    """
    # Validate project root before exposing any protocol capability. This also
    # rejects absent/non-dir .kg and prevents arbitrary DB-path injection.
    project_dir = H.resolve_paths(project_dir).root.parent
    server: Server = Server(SERVER_NAME, version=SERVER_VERSION)

    # ---- tools/list ------------------------------------------------------
    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        tools = []
        for name, spec in H.ALL_TOOLS.items():
            tools.append(types.Tool(
                name=name,
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            ))
        return tools

    # ---- tools/call ------------------------------------------------------
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> dict:
        if name not in H.ALL_TOOLS:
            raise H.HandlerError(
                -32601, f"unknown tool: {name}",
                safe_extra={"tool": name},
            )
        is_write = name in _WRITE_TOOL_NAMES
        if is_write and not allow_writes:
            # Default-denied structured error.
            err = H.HandlerError(
                -32601,
                f"tool '{name}' not authorized",
                safe_extra={
                    "reason": "server allow_writes=False",
                    "tool": name,
                },
            )
            # Return as structured isError result, not raised, so the SDK
            # serializes it into a proper CallToolResult envelope.
            return {
                "__is_error__": True,
                "error": _to_error_payload(err),
            }

        handler = _HANDLERS.get(name)
        if handler is None:  # pragma: no cover — registry is exhaustive
            raise H.HandlerError(-32601, f"tool not implemented: {name}")

        try:
            if is_write:
                # Pass explicit authorization (server-level flag) + embedder.
                result = handler(
                    project_dir,
                    embedder=embedder,
                    authorized=True,
                    **_coerce_args(name, arguments),
                )
            else:
                result = handler(project_dir, **_coerce_args(name, arguments))
        except H.HandlerError as exc:
            return {
                "__is_error__": True,
                "error": _to_error_payload(exc),
            }
        except (FileNotFoundError, ValueError) as exc:
            # Expected user-facing errors: gate rejections, missing config, etc.
            log.warning("tool %s failed: %s", name, exc)
            return {
                "__is_error__": True,
                "error": {
                    "code": -32602,
                    "message": f"{name} rejected",
                    "data": {"reason": type(exc).__name__},
                },
            }
        except Exception as exc:  # pragma: no cover — defensive last resort
            log.exception("tool %s crashed", name)
            return {
                "__is_error__": True,
                "error": {
                    "code": -32603,
                    "message": "internal error",
                    "data": {"reason": type(exc).__name__},
                },
            }
        return result

    # ---- resources/list --------------------------------------------------
    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                name=r["name"], uri=r["uri"],
                description=r["description"], mimeType=r["mimeType"],
            )
            for r in H.RESOURCES
        ]

    # ---- resources/read --------------------------------------------------
    @server.read_resource()
    async def read_resource(uri) -> list[types.TextResourceContents]:
        uri_str = str(uri).rstrip("/")
        # Pydantic AnyUrl canonicalizes authority-only URIs with a trailing
        # slash (``kg://ontology.json/``); our registry uses the human form.
        route = _RESOURCE_ROUTES.get(uri_str)
        if route is None:
            raise ValueError("unknown resource uri")
        handler, mime = route
        text = handler(project_dir)
        return [types.TextResourceContents(
            uri=uri, text=text, mimeType=mime,
        )]

    _patch_call_tool_for_errors(server)
    return server


# ---------------------------------------------------------------------------
# Dispatch table: tool name -> handler function
# ---------------------------------------------------------------------------

_HANDLERS = {
    # Reads
    "search_memory": H.search_memory,
    "expand_memory": H.expand_memory,
    "pack_context": H.pack_context,
    "dream_candidates": H.dream_candidates_tool,
    # Writes (authorization is injected by call_tool above)
    "save_pole": H.save_pole,
    "review_confirm": H.review_confirm,
    "review_reject": H.review_reject,
    "merge_nodes": H.merge_nodes,
}


def _coerce_args(name: str, arguments: dict) -> dict:
    """Project arguments to the handler's exact parameter names.

    MCP sends ``arguments`` as a dict matching the tool's ``inputSchema``.
    Each handler accepts the same keyword names, so we filter to only the
    keys it expects (defense in depth against extra keys sneaking through).
    """
    expected = _EXPECTED_ARGS[name]
    return {k: v for k, v in arguments.items() if k in expected}


_EXPECTED_ARGS: dict[str, set[str]] = {
    "search_memory": {"query", "mode", "k", "type_filter"},
    "expand_memory": {"seed_ids", "hops", "direction", "edge_types"},
    "pack_context": {"seed_ids", "hops", "budget_tokens"},
    "dream_candidates": {"since", "kind"},
    "save_pole": {"nodes", "edges", "source", "facts", "preferences"},
    "review_confirm": {"edge_id", "winner_id", "reason"},
    "review_reject": {"edge_id", "reason"},
    "merge_nodes": {"winner_id", "loser_id", "reason"},
}


# ---------------------------------------------------------------------------
# Result post-processing: the SDK wraps returned dicts into CallToolResult
# via call_tool's decorator. We need to translate our ``{"__is_error__": ...}``
# convention into a proper isError result. We do this by registering a
# post-hook on the Server — but the simplest path is a custom decorator-layer
# wrapper. Since the SDK does not expose that, we instead override the
# request handler the decorator installed, immediately.
# ---------------------------------------------------------------------------


def _patch_call_tool_for_errors(server: Server) -> None:
    """Wrap the SDK's CallToolRequest handler so our structured-error dict
    becomes a proper ``isError=True`` CallToolResult instead of being
    serialized as success content."""
    original = server.request_handlers[types.CallToolRequest]

    async def patched(req: types.CallToolRequest):
        result = await original(req)
        # The original handler returns types.ServerResult wrapping a
        # CallToolResult. We peek inside, detect our error envelope, and
        # rebuild the result.
        inner = getattr(result, "root", result)
        if not isinstance(inner, types.CallToolResult):
            return result
        sc = inner.structuredContent
        if isinstance(sc, dict) and sc.get("__is_error__"):
            err = sc.get("error", {})
            msg = json.dumps(err, ensure_ascii=False)
            return types.ServerResult(types.CallToolResult(
                content=[types.TextContent(type="text", text=msg)],
                structuredContent=err,
                isError=True,
            ))
        return result

    server.request_handlers[types.CallToolRequest] = patched


def build_server_with_patches(
    project_dir: Path | str,
    *,
    allow_writes: bool = False,
    embedder=None,
) -> Server:
    """Public build helper (kept for API symmetry; patches applied in build).

    Tests and ``main.py`` (T7) call this; ``build_server`` itself applies the
    structured-error patch so all callers see consistent error envelopes.
    """
    return build_server(project_dir, allow_writes=allow_writes, embedder=embedder)


# ---------------------------------------------------------------------------
# Stdio entrypoint — stdout = JSON-RPC frames ONLY.
# ---------------------------------------------------------------------------


async def _serve(project_dir: Path, allow_writes: bool) -> None:
    server = build_server_with_patches(project_dir, allow_writes=allow_writes)
    caps = server.get_capabilities(
        notification_options=NotificationOptions(),
        experimental_capabilities={},
    )
    init_options = types.InitializeResult(
        protocolVersion="2025-06-18",
        capabilities=caps,
        serverInfo=types.Implementation(name=SERVER_NAME, version=SERVER_VERSION),
    )
    async with stdio_server() as (read, write):
        await server.run(
            read, write,
            types.InitializeResult(
                protocolVersion=init_options.protocolVersion,
                capabilities=init_options.capabilities,
                serverInfo=init_options.serverInfo,
            ) and _init_options(server, allow_writes),
        )


def _init_options(server: Server, allow_writes: bool) -> "InitializationOptions":
    from mcp.server import InitializationOptions
    return InitializationOptions(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
        instructions=(
            "kg memory server. Read-only tools (search_memory, expand_memory, "
            "pack_context, dream_candidates) are always available. Write tools "
            f"(save_pole, review_confirm, review_reject, merge_nodes) are "
            f"{'ENABLED' if allow_writes else 'DISABLED'} on this server."
        ),
    )


def run_stdio(project_dir: Path | str, *, allow_writes: bool = False) -> None:
    """Synchronous entrypoint for ``kg mcp serve``.

    All diagnostics go to stderr; stdout is reserved for JSON-RPC frames.
    """
    # Belt-and-suspenders: never let any third-party logger write to stdout.
    for name in ("mcp", "kg.mcp", "kg"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.addHandler(logging.NullHandler())
        lg.propagate = False

    # Re-assert stdout is reserved for JSON-RPC — if a downstream lib hijacks
    # it we still want stderr for logs.
    if sys.stderr is None:  # pragma: no cover
        sys.stderr = open("/dev/tty" if sys.platform != "win32" else "CON", "w")

    try:
        project = Path(project_dir).expanduser().resolve()
        H.resolve_paths(project)  # fail-fast on bad project_dir
        asyncio.run(_serve(project, allow_writes))
    except H.HandlerError as exc:
        # Stderr-only diagnostic; stdout stays clean.
        print(f"kg mcp: {exc.message}", file=sys.stderr, flush=True)
        sys.exit(2)
    except KeyboardInterrupt:
        pass


__all__ = [
    "SERVER_NAME", "SERVER_VERSION",
    "build_server", "build_server_with_patches",
    "run_stdio",
]
