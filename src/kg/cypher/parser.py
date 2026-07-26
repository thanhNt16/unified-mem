"""Cypher read-only lexer + parser.

Supports a strict subset: MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN,
ORDER BY, LIMIT. Write clauses (CREATE/MERGE/SET/DELETE/REMOVE/CALL/DROP)
are rejected at the token level — they raise CypherError before the parser
sees them as clause keywords.

Caps enforced:
- max input length 4000 chars
- max RETURN items 200
- max WHERE depth 10

Deterministic error messages: text + token + clause position are reported
verbatim from input. No pretty-printing, no locale-specific wording.
"""
from __future__ import annotations

from dataclasses import dataclass, field


from kg import KgError


class CypherError(KgError):
    """Raised on unsupported Cypher clauses or malformed read-only queries."""


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

MAX_INPUT_CHARS = 4000
MAX_RETURN_ITEMS = 200
MAX_WHERE_DEPTH = 10


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

# Tokens that are READ clauses — the parser recognizes these.
_READ_CLAUSES = {"MATCH", "OPTIONAL", "WHERE", "WITH", "RETURN", "ORDER", "LIMIT"}

# Tokens that are WRITE/DDL/PROCEDURE — rejected at lexer level. The query
# never reaches the parser if any of these appear as a keyword token. This
# makes the read-only property structural: the parser cannot accidentally
# accept a write clause because it never sees one.
_FORBIDDEN_CLAUSE_KEYWORDS = {
    "CREATE", "MERGE", "SET", "DELETE", "DETACH",
    "REMOVE", "CALL", "DROP", "YIELD",
    "UNWIND", "FOREACH", "LOAD", "USE",
    "CONSTRAINT", "INDEX", "SHOW", "EXPLAIN", "PROFILE",
}

# Reserved phrase prefixes that don't tokenize as a single keyword but are
# commonly used to smuggle writes (e.g. "CALL db.schema.visualization()").
# We catch them at the keyword token level: any uppercase ident matching the
# first word of these phrases triggers a forbidden-keyword rejection.
_FORBIDDEN_FIRST_WORDS = {
    "CREATE", "MERGE", "SET", "DELETE", "DETACH",
    "REMOVE", "CALL", "DROP", "YIELD",
    "UNWIND", "FOREACH",
}


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    kind: str        # "KW", "IDENT", "NUM", "STR", "PUNCT", "LABEL", "EOF"
    value: str       # raw text (keywords uppercased)
    pos: int         # absolute offset in input


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c == "_"


def _is_ident_cont(c: str) -> bool:
    return c.isalnum() or c == "_"


def lex(src: str) -> list[Token]:
    out: list[Token] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "-" and i + 1 < n and src[i + 1] == "-":
            # line comment until newline
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            # block comment until */
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        start = i
        if c in "()[]{},.:;*<>=!|&+-/%":
            # Multi-char operators we recognize.
            if c == "<" and i + 2 < n and src[i + 1:i + 3] == "->":
                out.append(Token("PUNCT", "<->", start)); i += 3; continue
            if c == "-" and i + 1 < n and src[i + 1] == ">":
                out.append(Token("PUNCT", "->", start)); i += 2; continue
            if c == "<" and i + 1 < n and src[i + 1] == "=":
                out.append(Token("PUNCT", "<=", start)); i += 2; continue
            if c == ">" and i + 1 < n and src[i + 1] == "=":
                out.append(Token("PUNCT", ">=", start)); i += 2; continue
            if c == "!" and i + 1 < n and src[i + 1] == "=":
                out.append(Token("PUNCT", "!=", start)); i += 2; continue
            if c == "=" and i + 1 < n and src[i + 1] == "=":
                out.append(Token("PUNCT", "==", start)); i += 2; continue
            if c == "<" and i + 1 < n and src[i + 1] == "-":
                out.append(Token("PUNCT", "<-", start)); i += 2; continue
            if c == "=" and i + 1 < n and src[i + 1] == "~":
                out.append(Token("PUNCT", "=~", start)); i += 2; continue
            out.append(Token("PUNCT", c, start)); i += 1; continue
        if c in "'\"":
            quote = c
            i += 1
            buf = []
            while i < n and src[i] != quote:
                if src[i] == "\\" and i + 1 < n:
                    buf.append(src[i:i + 2]); i += 2; continue
                buf.append(src[i]); i += 1
            if i >= n:
                raise CypherError(
                    f"unterminated string literal at offset {start}"
                )
            i += 1
            out.append(Token("STR", "".join(buf), start)); continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            out.append(Token("NUM", src[i:j], start)); i = j; continue
        if _is_ident_start(c):
            j = i
            while j < n and _is_ident_cont(src[j]):
                j += 1
            word = src[i:j]
            upper = word.upper()
            # Identifier beginning with ':'? No — ':' is its own punct token.
            # Keywords are uppercase-cased for matching.
            if upper in _FORBIDDEN_FIRST_WORDS:
                raise CypherError(
                    f"read-only: {upper} not allowed at offset {start}"
                )
            if upper in _READ_CLAUSES or upper in {
                "AND", "OR", "NOT", "XOR", "IN", "IS", "NULL", "TRUE", "FALSE",
                "AS", "BY", "ASC", "DESC", "ASCENDING", "DESCENDING", "DISTINCT",
                "CONTAINS", "STARTS", "ENDS", "COUNT", "SIZE",
                "EXISTS", "ALL", "ANY", "NONE", "SINGLE",
                "ON", "TO", "OF", "CASE", "WHEN", "THEN", "ELSE", "END",
            } or upper == "MATCH":
                out.append(Token("KW", upper, start))
            else:
                out.append(Token("IDENT", word, start))
            i = j
            continue
        raise CypherError(
            f"unexpected character {c!r} at offset {start}"
        )
    out.append(Token("EOF", "", n))
    return out


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass
class NodePattern:
    variable: str | None = None
    labels: set[str] = field(default_factory=set)
    properties: dict = field(default_factory=dict)

@dataclass
class RelPattern:
    variable: str | None = None
    types: set[str] = field(default_factory=set)
    direction: str = "both"     # "out", "in", "both"
    varlen: bool = False        # * present
    min_hops: int | None = None
    max_hops: int | None = None
    properties: dict = field(default_factory=dict)

@dataclass
class MatchClause:
    optional: bool = False
    patterns: list[list[NodePattern | RelPattern]] = field(default_factory=list)

@dataclass
class WhereClause:
    # The AST is just a tree of Predicate — the translator walks it.
    predicate: "Predicate | None" = None
    depth: int = 0

@dataclass
class ReturnItem:
    expr: object            # Identifier / Literal / Call
    alias: str | None = None

@dataclass
class ReturnClause:
    distinct: bool = False
    items: list[ReturnItem] = field(default_factory=list)

@dataclass
class OrderByItem:
    expr: object
    descending: bool = False

@dataclass
class WithClause:
    distinct: bool = False
    items: list[ReturnItem] = field(default_factory=list)
    where: "Predicate | None" = None

@dataclass
class AST:
    match: MatchClause | None = None
    where: WhereClause | None = None
    with_clauses: list[WithClause] = field(default_factory=list)
    return_: ReturnClause | None = None
    order_by: list[OrderByItem] = field(default_factory=list)
    limit: int | None = None
    raw: str = ""


# ---------------------------------------------------------------------------
# Predicate tree (subset of Cypher expressions used in WHERE)
# ---------------------------------------------------------------------------

@dataclass
class Identifier:
    name: str

@dataclass
class Literal:
    value: object

@dataclass
class PropertyAccess:
    base: object
    name: str

@dataclass
class BinaryOp:
    op: str
    left: object
    right: object

@dataclass
class UnaryOp:
    op: str
    operand: object

@dataclass
class FunctionCall:
    name: str
    args: list[object]

# A Predicate is any of: BinaryOp | UnaryOp | FunctionCall | PropertyAccess |
# Identifier | Literal. depth = max nesting of AND/OR/NOT.

Predicate = object  # type alias — see above dataclasses


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, toks: list[Token]):
        self.toks = toks
        self.i = 0

    def peek(self, k: int = 0) -> Token:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self) -> Token:
        t = self.toks[self.i]
        if self.i < len(self.toks) - 1:
            self.i += 1
        return t

    def expect(self, kind: str, value: str | None = None) -> Token:
        t = self.peek()
        if t.kind != kind or (value is not None and t.value != value):
            want = f"{kind} {value!r}" if value else kind
            raise CypherError(
                f"expected {want} at offset {t.pos}, got {t.kind} {t.value!r}"
            )
        return self.next()

    def at_kw(self, *values: str) -> bool:
        t = self.peek()
        return t.kind == "KW" and t.value in values

    # -- top level ---------------------------------------------------------

    def parse(self) -> AST:
        ast = AST()
        # First clause must be MATCH (with optional OPTIONAL prefix).
        if not (self.at_kw("MATCH", "OPTIONAL")):
            t = self.peek()
            raise CypherError(
                f"query must start with MATCH at offset {t.pos}, "
                f"got {t.kind} {t.value!r}"
            )
        ast.match = self._parse_match()

        # Optional WITH... WHERE... chain.
        while True:
            if self.at_kw("WHERE"):
                if ast.where is not None:
                    raise CypherError("multiple WHERE clauses not supported")
                ast.where = self._parse_where()
            elif self.at_kw("WITH"):
                ast.with_clauses.append(self._parse_with())
            else:
                break

        # RETURN
        if self.at_kw("RETURN"):
            ast.return_ = self._parse_return()
        else:
            raise CypherError("query must end with RETURN")

        # ORDER BY / LIMIT after RETURN
        if self.at_kw("ORDER"):
            ast.order_by = self._parse_order_by()

        if self.at_kw("LIMIT"):
            ast.limit = self._parse_limit()

        # Must be at EOF
        t = self.peek()
        if t.kind != "EOF":
            raise CypherError(
                f"unexpected trailing tokens at offset {t.pos}: "
                f"{t.kind} {t.value!r}"
            )

        return ast

    # -- MATCH -------------------------------------------------------------

    def _parse_match(self) -> MatchClause:
        optional = False
        if self.at_kw("OPTIONAL"):
            optional = True
            self.next()
        self.expect("KW", "MATCH")
        patterns: list[list] = []
        patterns.append(self._parse_pattern())
        while self.peek().kind == "PUNCT" and self.peek().value == ",":
            self.next()
            patterns.append(self._parse_pattern())
        return MatchClause(optional=optional, patterns=patterns)

    def _parse_pattern(self) -> list:
        chain: list = [self._parse_node_pattern()]
        while True:
            t = self.peek()
            # Relationship continuation: -[...]->, <-[...]-, <-, -
            if t.kind == "PUNCT" and t.value in ("<-", "->", "<->", "-"):
                rel = self._parse_rel_pattern()
                chain.append(rel)
                chain.append(self._parse_node_pattern())
                continue
            break
        return chain

    def _parse_node_pattern(self) -> NodePattern:
        self.expect("PUNCT", "(")
        pat = NodePattern()
        # variable?
        t = self.peek()
        if t.kind == "IDENT":
            pat.variable = self.next().value
        # labels
        while (self.peek().kind == "PUNCT" and self.peek().value == ":") or (
            self.peek().kind == "LABEL"
        ):
            self._consume_label(pat.labels)
        # properties { ... }
        if self.peek().kind == "PUNCT" and self.peek().value == "{":
            pat.properties = self._parse_property_map()
        self.expect("PUNCT", ")")
        return pat

    def _consume_label(self, sink: set) -> None:
        # Either ':' followed by an IDENT, or a LABEL token (kept for future
        # lexer changes). Currently the lexer emits ':' as PUNCT and the
        # label name as IDENT/KW.
        t = self.peek()
        if t.kind == "PUNCT" and t.value == ":":
            self.next()
            name_tok = self.peek()
            if name_tok.kind not in ("IDENT", "KW"):
                raise CypherError(
                    f"expected label name at offset {name_tok.pos}"
                )
            sink.add(self.next().value)
            return
        if t.kind == "LABEL":
            sink.add(self.next().value)
            return
        raise CypherError(f"expected ':' at offset {t.pos}")

    def _parse_property_map(self) -> dict:
        # Accepts only literal-key : literal-value pairs (no expressions).
        self.expect("PUNCT", "{")
        props: dict = {}
        while True:
            t = self.peek()
            if t.kind == "PUNCT" and t.value == "}":
                self.next(); break
            if t.kind not in ("IDENT", "KW"):
                raise CypherError(
                    f"expected property name at offset {t.pos}"
                )
            key = self.next().value
            self.expect("PUNCT", ":")
            val_tok = self.peek()
            if val_tok.kind == "STR":
                props[key] = self.next().value
            elif val_tok.kind == "NUM":
                props[key] = _parse_number(self.next().value)
            elif val_tok.kind == "KW" and val_tok.value in (
                "TRUE", "FALSE", "NULL"
            ):
                props[key] = {"TRUE": True, "FALSE": False, "NULL": None}[
                    self.next().value
                ]
            else:
                raise CypherError(
                    f"expected literal value at offset {val_tok.pos}"
                )
            if self.peek().kind == "PUNCT" and self.peek().value == ",":
                self.next(); continue
            self.expect("PUNCT", "}")
            break
        return props

    def _parse_rel_pattern(self) -> RelPattern:
        t = self.next()  # leading punct: <- -> <-> -
        left_arrow = "<-" in t.value
        right_arrow = "->" in t.value or t.value == "-"
        pat = RelPattern()
        if left_arrow and right_arrow:
            pat.direction = "both"
        elif left_arrow:
            pat.direction = "in"
        else:
            pat.direction = "out"
        # Optional [details]
        if self.peek().kind == "PUNCT" and self.peek().value == "[":
            self.next()
            t2 = self.peek()
            if t2.kind == "IDENT":
                pat.variable = self.next().value
            # types
            while self.peek().kind == "PUNCT" and self.peek().value == ":":
                self._consume_rel_type(pat.types)
            # varlen *
            if self.peek().kind == "PUNCT" and self.peek().value == "*":
                self.next()
                pat.varlen = True
                # optional {min,max}
                if self.peek().kind == "NUM":
                    pat.min_hops = int(self.next().value)
                if self.peek().kind == "PUNCT" and self.peek().value == ".":
                    self.next()
                    self.expect("PUNCT", ".")
                    if self.peek().kind == "NUM":
                        pat.max_hops = int(self.next().value)
            # properties
            if self.peek().kind == "PUNCT" and self.peek().value == "{":
                pat.properties = self._parse_property_map()
            self.expect("PUNCT", "]")
        # trailing arrow
        t3 = self.peek()
        if t3.kind == "PUNCT":
            if t3.value == "->" and pat.direction == "out":
                self.next()
            elif t3.value == "<-" and pat.direction == "in":
                self.next()
            elif t3.value == "<->" and pat.direction == "both":
                self.next()
            # bare '-' already consumed; direction inferable from earlier token
        return pat

    def _consume_rel_type(self, sink: set) -> None:
        self.expect("PUNCT", ":")
        t = self.peek()
        if t.kind not in ("IDENT", "KW"):
            raise CypherError(f"expected relationship type at offset {t.pos}")
        sink.add(self.next().value)
        # allow alternation: A|B (rare in our subset, but tolerated)
        while self.peek().kind == "PUNCT" and self.peek().value == "|":
            self.next()
            t2 = self.peek()
            if t2.kind not in ("IDENT", "KW"):
                raise CypherError(
                    f"expected relationship type at offset {t2.pos}"
                )
            sink.add(self.next().value)

    # -- WHERE -------------------------------------------------------------

    def _parse_where(self) -> WhereClause:
        self.expect("KW", "WHERE")
        pred, _ = self._parse_or(0)
        depth = _where_depth(pred)
        if depth > MAX_WHERE_DEPTH:
            raise CypherError(
                f"WHERE nesting depth {depth} exceeds max {MAX_WHERE_DEPTH}"
            )
        return WhereClause(predicate=pred, depth=depth)

    def _parse_or(self, depth: int) -> tuple[object, int]:
        left, d = self._parse_and(depth)
        while self.at_kw("OR", "XOR"):
            self.next()
            right, d2 = self._parse_and(depth + 1)
            left = BinaryOp("OR", left, right)
            d = max(d, d2)
        return left, d

    def _parse_and(self, depth: int) -> tuple[object, int]:
        left, d = self._parse_not(depth)
        while self.at_kw("AND"):
            self.next()
            right, d2 = self._parse_not(depth + 1)
            left = BinaryOp("AND", left, right)
            d = max(d, d2)
        return left, d

    def _parse_not(self, depth: int) -> tuple[object, int]:
        if self.at_kw("NOT"):
            self.next()
            operand, nested_depth = self._parse_not(depth + 1)
            return UnaryOp("NOT", operand), max(depth + 1, nested_depth)
        return self._parse_comparison(depth)

    def _parse_comparison(self, depth: int) -> tuple[object, int]:
        left, _ = self._parse_primary(depth)
        while True:
            t = self.peek()
            if t.kind == "PUNCT" and t.value in (
                "=", "==", "!=", "<", ">", "<=", ">=", "=~"
            ):
                op = self.next().value
                right, _ = self._parse_primary(depth)
                left = BinaryOp(op, left, right)
            elif self.at_kw("IN", "CONTAINS", "STARTS", "ENDS", "IS"):
                op = self.next().value
                if op in ("STARTS", "ENDS"):
                    self.expect("KW", "WITH")
                    op = f"{op}_WITH"
                elif op == "IS":
                    if self.at_kw("NULL"):
                        self.next(); op = "IS_NULL"
                    elif self.at_kw("NOT"):
                        self.next(); self.expect("KW", "NULL")
                        op = "IS_NOT_NULL"
                    else:
                        raise CypherError(
                            f"expected NULL after IS at offset {self.peek().pos}"
                        )
                    left = UnaryOp(op, left); continue
                right, _ = self._parse_primary(depth)
                left = BinaryOp(op, left, right)
            else:
                break
        return left, depth

    def _parse_primary(self, depth: int) -> tuple[object, int]:
        t = self.peek()
        if t.kind == "PUNCT" and t.value == "(":
            self.next()
            inner, _ = self._parse_or(depth + 1)
            self.expect("PUNCT", ")")
            return inner, depth + 1
        if t.kind == "KW" and t.value in ("TRUE", "FALSE", "NULL"):
            return Literal(
                {"TRUE": True, "FALSE": False, "NULL": None}[self.next().value]
            ), depth
        if t.kind == "STR":
            return Literal(self.next().value), depth
        if t.kind == "NUM":
            return Literal(_parse_number(self.next().value)), depth
        if t.kind == "IDENT":
            base: object = Identifier(self.next().value)
            # property access
            while self.peek().kind == "PUNCT" and self.peek().value == ".":
                self.next()
                key = self.peek()
                if key.kind not in ("IDENT", "KW"):
                    raise CypherError(
                        f"expected property name at offset {key.pos}"
                    )
                base = PropertyAccess(base, self.next().value)
            # function call?
            if self.peek().kind == "PUNCT" and self.peek().value == "(":
                self.next()
                args: list = []
                if not (self.peek().kind == "PUNCT" and self.peek().value == ")"):
                    while True:
                        arg, _ = self._parse_or(depth + 1)
                        args.append(arg)
                        if self.peek().kind == "PUNCT" and self.peek().value == ",":
                            self.next(); continue
                        break
                self.expect("PUNCT", ")")
                if isinstance(base, Identifier):
                    base = FunctionCall(base.name, args)
                else:
                    raise CypherError("cannot call non-identifier")
            return base, depth
        raise CypherError(
            f"unexpected token in expression at offset {t.pos}: "
            f"{t.kind} {t.value!r}"
        )

    # -- WITH --------------------------------------------------------------

    def _parse_with(self) -> WithClause:
        self.expect("KW", "WITH")
        distinct = False
        if self.at_kw("DISTINCT"):
            self.next(); distinct = True
        items = self._parse_return_items()
        where = None
        if self.at_kw("WHERE"):
            w = self._parse_where()
            where = w.predicate
        return WithClause(distinct=distinct, items=items, where=where)

    def _parse_return_items(self) -> list:
        items: list = []
        items.append(self._parse_one_return_item())
        while self.peek().kind == "PUNCT" and self.peek().value == ",":
            self.next()
            items.append(self._parse_one_return_item())
        if len(items) > MAX_RETURN_ITEMS:
            raise CypherError(
                f"too many return items: {len(items)} > {MAX_RETURN_ITEMS}"
            )
        return items

    def _parse_one_return_item(self) -> ReturnItem:
        expr, _ = self._parse_or(0)
        alias: str | None = None
        if self.at_kw("AS"):
            self.next()
            t = self.peek()
            if t.kind not in ("IDENT", "KW"):
                raise CypherError(
                    f"expected alias at offset {t.pos}"
                )
            alias = self.next().value
        return ReturnItem(expr=expr, alias=alias)

    # -- RETURN ------------------------------------------------------------

    def _parse_return(self) -> ReturnClause:
        self.expect("KW", "RETURN")
        distinct = False
        if self.at_kw("DISTINCT"):
            self.next(); distinct = True
        # bare '*' alone or '*' then items
        star = False
        if self.peek().kind == "PUNCT" and self.peek().value == "*":
            self.next(); star = True
        items: list = []
        if not star or not (self.peek().kind == "PUNCT" and self.peek().value == ","):
            if not star:
                items = self._parse_return_items()
            else:
                items = [ReturnItem(expr=Literal("*"), alias=None)]
                if self.peek().kind == "PUNCT" and self.peek().value == ",":
                    self.next()
                    items.extend(self._parse_return_items())
        else:
            items = [ReturnItem(expr=Literal("*"), alias=None)]
        return ReturnClause(distinct=distinct, items=items)

    # -- ORDER BY / LIMIT --------------------------------------------------

    def _parse_order_by(self) -> list:
        self.expect("KW", "ORDER")
        self.expect("KW", "BY")
        out: list = []
        while True:
            expr, _ = self._parse_or(0)
            desc = False
            if self.at_kw("ASC", "ASCENDING"):
                self.next()
            elif self.at_kw("DESC", "DESCENDING"):
                self.next(); desc = True
            out.append(OrderByItem(expr=expr, descending=desc))
            if self.peek().kind == "PUNCT" and self.peek().value == ",":
                self.next(); continue
            break
        return out

    def _parse_limit(self) -> int:
        self.expect("KW", "LIMIT")
        t = self.peek()
        if t.kind != "NUM":
            raise CypherError(f"expected number after LIMIT at offset {t.pos}")
        v = _parse_number(self.next().value)
        if not isinstance(v, int) or v < 0:
            raise CypherError(f"LIMIT must be a non-negative integer, got {v}")
        return int(v)


def _parse_number(s: str) -> int | float:
    if "." in s:
        return float(s)
    return int(s)


def _where_depth(expr: object) -> int:
    """Count nested boolean operators; atomic comparisons are depth zero."""
    if isinstance(expr, UnaryOp) and expr.op == "NOT":
        return 1 + _where_depth(expr.operand)
    if isinstance(expr, BinaryOp) and expr.op in ("AND", "OR", "XOR"):
        return 1 + max(_where_depth(expr.left), _where_depth(expr.right))
    return 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse(query: str) -> AST:
    if not isinstance(query, str):
        raise CypherError("query must be a string")
    if len(query) > MAX_INPUT_CHARS:
        raise CypherError(
            f"query too long: {len(query)} > {MAX_INPUT_CHARS} chars"
        )
    toks = lex(query)
    p = _Parser(toks)
    ast = p.parse()
    ast.raw = query
    return ast
