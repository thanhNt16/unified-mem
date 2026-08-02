import json
from dataclasses import asdict
from math import isfinite

from kg.viz.layout3d import (
    LayoutEdgeInput,
    LayoutNodeInput,
    fnv1a_32,
    layout_graph,
)


def _nodes() -> list[LayoutNodeInput]:
    return [
        LayoutNodeInput("b", "fact", "Beta", "fact", 1),
        LayoutNodeInput("a", "document/docs", "Alpha", "document", 2),
        LayoutNodeInput("c", "fact", "Gamma", "fact", 1),
    ]


def _edges() -> list[LayoutEdgeInput]:
    return [
        LayoutEdgeInput("a", "b", "mentions"),
        LayoutEdgeInput("missing", "a", "mentions"),
        LayoutEdgeInput("b", "c", "related_to"),
    ]


def test_fnv1a_matches_upstream_unsigned_32_bit_hash() -> None:
    assert fnv1a_32(b"") == 0x811C9DC5
    assert fnv1a_32(b"fact") == 0x26B603F


def test_layout_is_deterministic_finite_and_stably_numbered() -> None:
    first = layout_graph(_nodes(), _edges())
    second = layout_graph(list(reversed(_nodes())), list(reversed(_edges())))

    assert first == second
    assert [(n.kg_id, n.render_id) for n in first.nodes] == [
        ("a", 0), ("b", 1), ("c", 2)
    ]
    assert [(e.source, e.target, e.type) for e in first.edges] == [
        (0, 1, "mentions"), (1, 2, "related_to")
    ]
    assert all(isfinite(v) for n in first.nodes for v in (n.x, n.y, n.z, n.size))


def test_layout_has_no_fabricated_call_depth_layer() -> None:
    result = layout_graph(_nodes(), _edges())
    assert all(node.z == 0.0 for node in result.nodes)


def test_layout_serialization_is_byte_stable() -> None:
    result = layout_graph(_nodes(), _edges())
    encoded = json.dumps(
        {
            "nodes": [asdict(node) for node in result.nodes],
            "edges": [asdict(edge) for edge in result.edges],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert encoded == json.dumps(json.loads(encoded), sort_keys=True, separators=(",", ":"))
