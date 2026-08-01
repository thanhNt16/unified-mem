from __future__ import annotations
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "code_index"


def test_python_projection_emits_file_class_function_and_containment():
    """Python parser emits file, class, function with containment edges."""
    from kg.code_index import project_code_file

    projection = project_code_file(
        FIXTURES / "sample.py", "demo", "sha256", "v1"
    )

    # Node IDs deterministic: code:demo:sample.py, code:demo:sample.py#Greeter, etc.
    node_keys = {(n.subtype, n.qualified_name) for n in projection.nodes}
    assert node_keys >= {
        ("code_file", "sample.py"),
        ("class", "sample.py:Greeter"),
        ("function", "sample.py:Greeter.greet"),
        ("function", "sample.py:main"),
    }

    # Containment: functions/classes -> file, methods -> class
    edge_types = {e.semantic_type for e in projection.edges}
    assert "part_of" in edge_types

    # File node first
    assert projection.nodes[0].subtype == "code_file"
    assert projection.nodes[0].attributes["path"] == "sample.py"
    assert projection.nodes[0].attributes["sha256"] == "sha256"
    assert projection.nodes[0].attributes["parser_version"] == "v1"


def test_typescript_projection_emits_exported_function_and_class():
    """TypeScript parser emits exported class/function."""
    from kg.code_index import project_code_file

    projection = project_code_file(
        FIXTURES / "sample.ts", "demo", "sha256", "v1"
    )

    qualified_names = {n.qualified_name for n in projection.nodes}
    assert qualified_names >= {
        "sample.ts",
        "sample.ts:Api",
        "sample.ts:Api.fetchUser",
    }

    # TS class and exported function present
    subtypes = {n.subtype for n in projection.nodes}
    assert "class" in subtypes
    assert "function" in subtypes


def test_javascript_projection_emits_function_and_class():
    """JavaScript parser emits class and function."""
    from kg.code_index import project_code_file

    projection = project_code_file(
        FIXTURES / "sample.js", "demo", "sha256", "v1"
    )

    qualified_names = {n.qualified_name for n in projection.nodes}
    assert qualified_names >= {
        "sample.js",
        "sample.js:Calculator",
        "sample.js:Calculator.add",
    }


def test_projection_id_format_matches_spec():
    """ID deterministic: code:{project}:{rel_path}#{qualified_name}."""
    from kg.code_index import project_code_file

    projection = project_code_file(
        FIXTURES / "sample.py", "myproject", "sha256", "v1"
    )

    # File node uses rel_path only (no # segment)
    file_node = next(n for n in projection.nodes if n.subtype == "code_file")
    assert file_node.id == "code:myproject:sample.py"

    # Class/function use #qualified_name suffix
    class_node = next(n for n in projection.nodes if n.subtype == "class")
    assert class_node.id == "code:myproject:sample.py#Greeter"

    func_node = next(
        n for n in projection.nodes
        if n.subtype == "function" and "Greeter" in n.qualified_name
    )
    assert func_node.id == "code:myproject:sample.py#Greeter.greet"


def test_edge_part_of_containment_structure():
    """part_of edges: class/file -> file, method -> class."""
    from kg.code_index import project_code_file

    projection = project_code_file(
        FIXTURES / "sample.py", "demo", "sha256", "v1"
    )

    # Map source_id -> [(semantic_type, target_id)]
    outgoing = {}
    for e in projection.edges:
        outgoing.setdefault(e.from_id, []).append((e.semantic_type, e.to_id))

    # Class -> file
    class_node = next(n for n in projection.nodes if n.subtype == "class")
    assert ("part_of", "code:demo:sample.py") in outgoing.get(class_node.id, [])

    # Method -> class
    method_node = next(
        n for n in projection.nodes
        if n.subtype == "function" and ".greet" in n.qualified_name
    )
    assert ("part_of", class_node.id) in outgoing.get(method_node.id, [])
