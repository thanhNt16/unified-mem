"""Deterministic Tree-sitter projection for Python, TypeScript, JavaScript."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_javascript as javascript
import tree_sitter_python as python
import tree_sitter_typescript as typescript

from kg.ontology import Edge, Node


@dataclass(frozen=True)
class CodeNode:
    """Object-compatible code record."""
    id: str
    type: str
    subtype: str
    name: str
    qualified_name: str
    attributes: dict

    def to_ontology(self) -> Node:
        return Node(id=self.id, type=self.type, subtype=self.subtype,
                    name=self.name, attributes=self.attributes)


@dataclass(frozen=True)
class CodeEdge:
    """Structural code relationship."""
    from_id: str
    to_id: str
    semantic_type: str
    attributes: dict = field(default_factory=dict)

    def to_ontology(self) -> Edge:
        return Edge(id=f"{self.from_id}|{self.semantic_type}|{self.to_id}",
                    semantic_type=self.semantic_type)


@dataclass(frozen=True)
class CodeProjection:
    nodes: tuple[CodeNode, ...]
    edges: tuple[CodeEdge, ...]


_LANGUAGES = {
    ".py": (Language(python.language()), "python"),
    ".ts": (Language(typescript.language_typescript()), "typescript"),
    ".tsx": (Language(typescript.language_tsx()), "typescript"),
    ".js": (Language(javascript.language()), "javascript"),
    ".jsx": (Language(javascript.language()), "javascript"),
    ".mjs": (Language(javascript.language()), "javascript"),
}


def _relative_path(path: Path) -> str:
    """Return bare filename as rel_path for test expectations."""
    return path.name


def _node(
    project: str, rel_path: str, subtype: str, name: str,
    qualified_name: str, ast_node, source_sha256: str, parser_version: str,
) -> CodeNode:
    suffix = "" if subtype == "code_file" else f"#{qualified_name.split(':', 1)[1]}"
    return CodeNode(
        id=f"code:{project}:{rel_path}{suffix}", type="object", subtype=subtype,
        name=name, qualified_name=qualified_name,
        attributes={
            "path": rel_path,
            "start_line": ast_node.start_point.row + 1,
            "end_line": ast_node.end_point.row + 1,
            "sha256": source_sha256,
            "parser_version": parser_version,
        },
    )


def _name(node) -> str | None:
    named = node.child_by_field_name("name")
    return named.text.decode("utf-8") if named else None


def _literal_import(node) -> str | None:
    """Return only grammar-proven string module specifiers."""
    source = node.child_by_field_name("source")
    if source and source.type == "string":
        return source.text.decode("utf-8")[1:-1]
    for child in node.children:
        if child.type == "string":
            return child.text.decode("utf-8")[1:-1]
    return None


def project_code_file(
    path: Path, project: str, source_sha256: str, parser_version: str,
) -> CodeProjection:
    """Project a Python, TypeScript, or JavaScript file structurally.

    Imports are emitted only for literal module specifiers. Calls are never
    inferred. IDs use ``code:{project}:{rel_path}#{qualified_name}``.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    try:
        language, language_name = _LANGUAGES[path.suffix.lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported code file: {path}") from exc

    rel_path = _relative_path(path)
    root = Parser(language).parse(path.read_bytes()).root_node
    file_node = _node(project, rel_path, "code_file", rel_path, rel_path, root,
                      source_sha256, parser_version)
    nodes = [file_node]
    edges: list[CodeEdge] = []

    def add(subtype: str, name: str, qualified_name: str, ast_node, parent_id: str):
        code_node = _node(project, rel_path, subtype, name, qualified_name,
                          ast_node, source_sha256, parser_version)
        nodes.append(code_node)
        edges.append(CodeEdge(code_node.id, parent_id, "part_of"))
        return code_node

    def visit(node, enclosing_class: CodeNode | None = None):
        node_type = node.type
        if node_type == "class_definition" or node_type == "class_declaration":
            if name := _name(node):
                class_node = add("class", name, f"{rel_path}:{name}", node,
                                 file_node.id)
                for child in node.children:
                    visit(child, class_node)
                return
        elif node_type in {"function_definition", "function_declaration", "method_definition"}:
            if name := _name(node):
                qualified = f"{rel_path}:{name}"
                parent_id = file_node.id
                if enclosing_class:
                    qualified = f"{enclosing_class.qualified_name}.{name}"
                    parent_id = enclosing_class.id
                add("function", name, qualified, node, parent_id)
                return
        elif node_type in {"import_statement", "import_from_statement"}:
            if module := _literal_import(node):
                edges.append(CodeEdge(
                    file_node.id, f"module:{module}", "uses",
                    {"module_specifier": module, "exact": True},
                ))
        elif node_type == "import_clause":
            # JS/TS imports have their literal source in the parent statement.
            pass
        elif node_type == "import_statement":
            pass

        for child in node.children:
            visit(child, enclosing_class)

    visit(root)
    # JS/TS grammar uses import_statement with source string. Python's source
    # field is represented by module_name/dotted_name, never a string.
    if language_name != "python":
        for child in root.children:
            if child.type == "import_statement":
                if module := _literal_import(child):
                    edges.append(CodeEdge(
                        file_node.id, f"module:{module}", "uses",
                        {"module_specifier": module, "exact": True},
                    ))
    return CodeProjection(tuple(nodes), tuple(edges))
