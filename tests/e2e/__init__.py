"""E2E harness for kg distribution testing.

Contains:
- SessionDriver ABC: spawn a harness binary, send a message, wait for graph state.
- assert_graph_shape: assert node/edge counts in a band + ontology conformance.
- kg_tool_json_equal: order-independent structural equality for kg tool JSON.
- mcp_stdio_session + McpStdioClient: Layer-1 MCP stdio transport.
- SkipLayer2: signal a live-LLM test should be skipped.

Determinism: no LLM in this library. LLM-driving is the harness driver's
concern; this library only handles process spawn, env scrubbing, and assertions
over the resulting kg.db.
"""
from tests.e2e.harness import (
    CleanEnvError,
    McpStdioClient,
    SessionDriver,
    SkipLayer2,
    build_clean_env,
    mcp_stdio_session,
)
from tests.e2e.assertions import (
    GraphShape,
    ToolJsonMismatch,
    assert_graph_shape,
    kg_tool_json_equal,
)

__all__ = [
    "SessionDriver",
    "CleanEnvError",
    "build_clean_env",
    "mcp_stdio_session",
    "McpStdioClient",
    "SkipLayer2",
    "assert_graph_shape",
    "kg_tool_json_equal",
    "GraphShape",
    "ToolJsonMismatch",
]
