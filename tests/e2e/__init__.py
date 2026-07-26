"""E2E harness for kg distribution testing.

Contains:
- SessionDriver ABC: spawn a harness binary, send a message, wait for graph state.
- assert_graph_shape: assert node/edge counts in a band + ontology conformance.
- kg_tool_json_equal: order-independent structural equality for kg tool JSON.

Determinism: no LLM in this library. LLM-driving is the harness driver's
concern; this library only handles process spawn, env scrubbing, and assertions
over the resulting kg.db.
"""
from tests.e2e.harness import (
    SessionDriver,
    CleanEnvError,
    build_clean_env,
)
from tests.e2e.assertions import (
    assert_graph_shape,
    kg_tool_json_equal,
    GraphShape,
    ToolJsonMismatch,
)

__all__ = [
    "SessionDriver",
    "CleanEnvError",
    "build_clean_env",
    "assert_graph_shape",
    "kg_tool_json_equal",
    "GraphShape",
    "ToolJsonMismatch",
]
