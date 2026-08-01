"""
Unified Node Envelope Schema for KG Memory Layer

This schema provides a single envelope format that can represent nodes from:
- codebase-memory-mcp (Function, Method, Route, Class, Variable nodes)
- Graphify (code, document, paper, image, rationale, concept nodes)
- AgentMemory (observations, episodes, decisions, preferences)
- KG Wiki (person, organization, location, event, object, fact, preference, chunk, document)

All upstream systems map their native representation into this common envelope.
The envelope preserves source lineage while enabling cross-system queries and operations.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, Field


class Authority(str, Enum):
    """Source authority levels - who/what created this node"""

    # Deterministic extraction from source code/docs
    DERIVED_DETERMINISTIC = "derived_deterministic"

    # Human-reviewed wiki content (reviewed, approved knowledge)
    REVIEWED_WIKI = "reviewed_wiki"

    # Agent observations during sessions (LLM-derived, may be noisy)
    AGENT_OBSERVATION = "agent_observation"

    # User-provided facts/preferences (explicit user input)
    USER_PROVIDED = "user_provided"

    # External data source (APIs, databases, third-party)
    EXTERNAL_SOURCE = "external_source"


class CanonicalType(str, Enum):
    """Type of canonical reference - where does this node point to?"""

    # Source code span (file:line:col)
    SOURCE_SPAN = "source_span"

    # Wiki page (wiki/entities/<name>.md)
    WIKI_SPAN = "wiki_span"

    # AgentMemory observation ID
    AGENT_MEMORY_OBSERVATION = "agentmemory_observation"

    # External URL reference
    EXTERNAL_URL = "external_url"

    # Document chunk reference
    DOCUMENT_CHUNK = "document_chunk"

    # No canonical reference (synthesized/fact-only)
    NONE = "none"


class NodeType(str, Enum):
    """Unified node type taxonomy across all systems"""

    # ===== KG Native Types =====
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    EVENT = "event"
    OBJECT = "object"
    PREFERENCE = "preference"
    FACT = "fact"
    DOCUMENT = "document"
    CHUNK = "chunk"
    CONVERSATION = "conversation"
    SESSION = "session"

    # ===== Codebase-memory-mcp Types =====
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    ROUTE = "route"
    VARIABLE = "variable"
    PACKAGE = "package"
    MODULE = "module"

    # ===== Graphify Types =====
    CODE = "code"  # Maps to function/method/class
    PAPER = "paper"  # Academic paper
    IMAGE = "image"  # Image/figure
    RATIONALE = "rationale"  # Design rationale
    CONCEPT = "concept"  # Domain concept

    # ===== AgentMemory Types =====
    EPISODE = "episode"  # Multi-turn conversation episode
    DECISION = "decision"  # Architectural/implementation decision
    OBSERVATION = "observation"  # Single observation
    PATTERN = "pattern"  # Learned pattern
    WORKFLOW = "workflow"  # Discovered workflow


class CanonicalReference(BaseModel):
    """Pointer to the canonical source of this node"""

    type: CanonicalType
    ref: str  # The actual reference (file path, wiki path, URL, etc.)
    span: str | None = None  # Line/column, page, timestamp, etc.

    # For source_span: structured location data
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    start_column: int | None = None
    end_column: int | None = None

    # For wiki_span: wiki path
    wiki_path: str | None = None

    # For agentmemory_observation: observation ID
    observation_id: str | None = None

    # For external_url: full URL
    url: str | None = None

    # For document_chunk: chunk reference
    doc_path: str | None = None
    chunk_index: int | None = None


class Provenance(BaseModel):
    """Lineage and extraction metadata"""

    # Which extractor created this node
    extractor: str  # "codebase-memory-mcp", "graphify", "kg:extract", "agentmemory"

    # Extractor version (for compatibility)
    extractor_version: str = "1.0"

    # Activity/context that triggered extraction
    activity: str | None = None  # "session-abc", "pr-123", "manual-ingest", etc.

    # Source lineage (trace back to raw inputs)
    sources: list[dict] = Field(default_factory=list)
    # Example: [{"doc": "raw/2026-07-26--pdf--paper.md", "chunk": 3}]

    # When this node was first created
    created_at: datetime | None = None

    # When this node was last modified
    updated_at: datetime | None = None


class NodeEnvelope(BaseModel):
    """
    Unified node envelope representing any node from any upstream system.

    All systems map their native representation into this format.
    The envelope preserves enough lineage to reconstruct the original node
    while enabling cross-system queries and operations.
    """

    # ===== Identity =====

    # Namespaced node ID: "<namespace>:<type>:<name-slug>"
    # Examples:
    #   "codebase-memory-mcp:function:verify_token"
    #   "graphify:concept:attention-mechanism"
    #   "kg:person:demis-hassabis"
    #   "agentmemory:decision:use-sqlite"
    node_id: str

    # Namespace/scope this node belongs to
    # Typically: repo name, project name, or "global"
    namespace: str

    # Unified node type from NodeType enum
    node_type: NodeType

    # Optional domain-specific subtype
    # Example: for object -> "software", "document", "task"
    # Example: for person -> "individual", "group"
    subtype: str | None = None

    # ===== Content =====

    # Human-readable title/name
    title: str

    # Rich description (1-3 paragraphs)
    description: str | None = None

    # Short summary (1-2 sentences)
    summary: str | None = None

    # Detailed content (code body, full text, etc.)
    content: str | None = None

    # ===== Metadata =====

    # Source authority level
    authority: Authority = Authority.AGENT_OBSERVATION

    # Confidence score (0-1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # Temporal validity for facts/preferences
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    # ===== Canonical Reference =====

    canonical: CanonicalReference

    # ===== Lineage =====

    provenance: Provenance

    # ===== Extended Attributes =====

    # System-specific attributes that don't fit in common fields
    # Each system can store its own metadata here
    attributes: dict[str, Any] = Field(default_factory=dict)

    # Embedding vector (full-context for search/dedup)
    embedding: list[float] | None = None

    # ===== Status =====

    # Node lifecycle status
    status: Literal["active", "tombstoned", "pending_review"] = "active"

    # If tombstoned/pending, what happened?
    status_reason: str | None = None

    # If merged into another node, point to it
    merged_into: str | None = None


# ============================================================================
# Mapping Examples
# ============================================================================

def from_codebase_memory_mcp(
    qualified_name: str,
    node_type: str,
    file_path: str,
    start_line: int,
    end_line: int,
    summary: str,
    content: str,
    attributes: dict,
) -> NodeEnvelope:
    """
    Map codebase-memory-mcp node to unified envelope.

    Example:
        qualified_name = "repo-a:src/auth/token.ts#verify_token"
        node_type = "Function"
        file_path = "src/auth/token.ts"
        start_line = 42
        end_line = 58
        summary = "Validates JWT token and returns decoded payload"
        content = "function verify_token(token: string): Promise<DecodedToken> { ... }"
        attributes = {"complexity": 5, "is_async": True, "params": ["token"]}
    """
    # Parse qualified_name: "repo:module:file:symbol#member"
    parts = qualified_name.split("#")
    base_name = parts[0]
    member = parts[1] if len(parts) > 1 else None

    # Extract namespace from qualified_name
    namespace = base_name.split(":")[0] if ":" in base_name else "unknown"

    # Create name slug for node_id
    name_slug = member if member else base_name.split("/")[-1]
    name_slug = name_slug.lower().replace("_", "-").replace(".", "-")

    return NodeEnvelope(
        node_id=f"codebase-memory-mcp:{node_type.value if isinstance(node_type, NodeType) else node_type}:{name_slug}",
        namespace=namespace,
        node_type=NodeType(node_type.lower()) if node_type.lower() in [t.value for t in NodeType] else NodeType.FUNCTION,
        subtype=None,
        title=member if member else base_name,
        description=summary,
        summary=summary,
        content=content,
        authority=Authority.DERIVED_DETERMINISTIC,
        confidence=1.0,  # Code extraction is deterministic
        canonical=CanonicalReference(
            type=CanonicalType.SOURCE_SPAN,
            ref=f"{file_path}:{start_line}-{end_line}",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
        ),
        provenance=Provenance(
            extractor="codebase-memory-mcp",
            extractor_version="1.0",
            activity=None,
            sources=[{"file": file_path, "lines": f"{start_line}-{end_line}"}],
            created_at=datetime.utcnow(),
        ),
        attributes=attributes,
        status="active",
    )


def from_graphify(
    node_type: str,
    title: str,
    description: str,
    source_path: str | None = None,
    metadata: dict | None = None,
) -> NodeEnvelope:
    """
    Map Graphify node to unified envelope.

    Example:
        node_type = "concept"
        title = "Attention Mechanism"
        description = "Neural network attention allows..."
        source_path = "papers/attention-is-all-you-need.pdf"
        metadata = {"paper_year": 2017, "authors": ["Vaswani et al."]}
    """
    name_slug = title.lower().replace(" ", "-").replace("/", "-")[:80]
    namespace = "graphify"

    return NodeEnvelope(
        node_id=f"graphify:{node_type}:{name_slug}",
        namespace=namespace,
        node_type=NodeType(node_type) if node_type in [t.value for t in NodeType] else NodeType.CONCEPT,
        subtype=None,
        title=title,
        description=description,
        summary=description[:200] if description else None,
        content=None,
        authority=Authority.AGENT_OBSERVATION,
        confidence=0.8,
        canonical=CanonicalReference(
            type=CanonicalType.SOURCE_SPAN if source_path else CanonicalType.NONE,
            ref=source_path or "",
        ),
        provenance=Provenance(
            extractor="graphify",
            extractor_version="1.0",
            activity=None,
            sources=[{"doc": source_path}] if source_path else [],
            created_at=datetime.utcnow(),
        ),
        attributes=metadata or {},
        status="active",
    )


def from_agentmemory(
    observation_type: str,
    content: str,
    session_id: str,
    timestamp: datetime,
    confidence: float = 0.7,
    metadata: dict | None = None,
) -> NodeEnvelope:
    """
    Map AgentMemory observation to unified envelope.

    Example:
        observation_type = "decision"
        content = "Use SQLite for graph storage instead of Neo4j"
        session_id = "session-abc-123"
        timestamp = datetime(2026, 7, 26, 14, 30)
        confidence = 0.8
        metadata = {"reasoning": "Local-first requirement"}
    """
    # Create slug from content (first 6 words)
    words = content.lower().split()[:6]
    name_slug = "-".join(words)

    return NodeEnvelope(
        node_id=f"agentmemory:{observation_type}:{name_slug}",
        namespace=session_id,
        node_type=NodeType(observation_type) if observation_type in [t.value for t in NodeType] else NodeType.OBSERVATION,
        subtype=None,
        title=content[:80],
        description=content,
        summary=content[:200],
        content=content,
        authority=Authority.AGENT_OBSERVATION,
        confidence=confidence,
        canonical=CanonicalReference(
            type=CanonicalType.AGENT_MEMORY_OBSERVATION,
            ref=f"session:{session_id}",
            observation_id=f"{session_id}:{timestamp.isoformat()}",
        ),
        provenance=Provenance(
            extractor="agentmemory",
            extractor_version="1.0",
            activity=session_id,
            sources=[],
            created_at=timestamp,
        ),
        attributes=metadata or {},
        status="active",
    )


def from_kg_native(
    node_type: str,
    name: str,
    summary: str | None,
    wiki_path: str | None,
    attributes: dict,
    sources: list[dict],
) -> NodeEnvelope:
    """
    Map native KG node (from ontology.py) to unified envelope.

    Example:
        node_type = "person"
        name = "Demis Hassabis"
        summary = "CEO of DeepMind, Nobel laureate"
        wiki_path = "wiki/entities/demis-hassabis.md"
        attributes = {"affiliation": "DeepMind", "awards": ["Nobel", "Knighthood"]}
        sources = [{"doc": "raw/2026-07-26--text--hassabis.md", "chunk": 3}]
    """
    name_slug = name.lower().replace(" ", "-").replace(".", "-")[:80]

    return NodeEnvelope(
        node_id=f"kg:{node_type}:{name_slug}",
        namespace="kg",
        node_type=NodeType(node_type),
        subtype=attributes.get("subtype"),
        title=name,
        description=summary,
        summary=summary,
        content=None,
        authority=Authority.REVIEWED_WIKI if wiki_path else Authority.AGENT_OBSERVATION,
        confidence=0.9 if wiki_path else 0.7,
        canonical=CanonicalReference(
            type=CanonicalType.WIKI_SPAN if wiki_path else CanonicalType.NONE,
            ref=wiki_path or "",
            wiki_path=wiki_path,
        ),
        provenance=Provenance(
            extractor="kg:extract",
            extractor_version="1.0",
            activity=None,
            sources=sources,
            created_at=datetime.utcnow(),
        ),
        attributes=attributes,
        status="active",
    )


# ============================================================================
# Validation & Utilities
# ============================================================================

def validate_envelope(envelope: NodeEnvelope) -> list[str]:
    """
    Validate envelope for common issues.
    Returns list of warning messages (empty if valid).
    """
    warnings = []

    # Check node_id format
    if not envelope.node_id.startswith(f"{envelope.provenance.extractor}:"):
        warnings.append(f"node_id should start with '{envelope.provenance.extractor}:'")

    # Check confidence range
    if not 0 <= envelope.confidence <= 1:
        warnings.append("confidence must be between 0 and 1")

    # Check temporal consistency
    if envelope.valid_to and envelope.valid_from and envelope.valid_to <= envelope.valid_from:
        warnings.append("valid_to must be after valid_from")

    # Check canonical reference completeness
    if envelope.canonical.type == CanonicalType.SOURCE_SPAN and not envelope.canonical.file_path:
        warnings.append("source_span requires file_path")

    if envelope.canonical.type == CanonicalType.WIKI_SPAN and not envelope.canonical.wiki_path:
        warnings.append("wiki_span requires wiki_path")

    # Check status consistency
    if envelope.status == "tombstoned" and not envelope.merged_into:
        warnings.append("tombstoned nodes should have merged_into")

    return warnings


def serialize_for_storage(envelope: NodeEnvelope) -> dict:
    """Serialize envelope for storage in KG database"""
    return envelope.model_dump(mode="json", exclude_none=True)


def deserialize_from_storage(data: dict) -> NodeEnvelope:
    """Deserialize envelope from KG database"""
    return NodeEnvelope.model_validate(data)
