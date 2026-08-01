"""
Unified Node Envelope - Concrete Mapping Examples

This file shows 5 concrete examples of how each upstream system maps their
native node representation into the unified envelope format.
"""

from datetime import datetime
from src.kg.unified import (
    NodeEnvelope,
    CanonicalReference,
    Provenance,
    Authority,
    CanonicalType,
    NodeType,
    from_codebase_memory_mcp,
    from_graphify,
    from_agentmemory,
    from_kg_native,
)


# ============================================================================
# Example 1: codebase-memory-mcp - Function Node
# ============================================================================

example_codebase_node = from_codebase_memory_mcp(
    qualified_name="my-auth-service:src/auth/jwt.ts#verify_token",
    node_type="function",
    file_path="src/auth/jwt.ts",
    start_line=42,
    end_line=58,
    summary="Validates JWT token, checks expiration, and returns decoded payload",
    content="""function verify_token(token: string): Promise<DecodedToken> {
  const decoded = jwt.verify(token, process.env.JWT_SECRET);
  if (decoded.exp < Date.now() / 1000) {
    throw new Error('Token expired');
  }
  return decoded;
}""",
    attributes={
        "complexity": 3,
        "is_async": True,
        "params": ["token: string"],
        "returns": "Promise<DecodedToken>",
        "throws": ["Error"],
    },
)

# Result:
# node_id: "codebase-memory-mcp:function:verify-token"
# namespace: "my-auth-service"
# node_type: NodeType.FUNCTION
# authority: Authority.DERIVED_DETERMINISTIC
# confidence: 1.0
# canonical: {type: SOURCE_SPAN, ref: "src/auth/jwt.ts:42-58", file_path: "src/auth/jwt.ts", start_line: 42, end_line: 58}


# ============================================================================
# Example 2: Graphify - Concept Node from Paper
# ============================================================================

example_graphify_concept = from_graphify(
    node_type="concept",
    title="Self-Attention Mechanism",
    description="Neural network self-attention allows each position in a sequence to attend to all other positions, computing attention scores as scaled dot-product of query and key vectors.",
    source_path="papers/attention-is-all-you-need.pdf",
    metadata={
        "paper_title": "Attention Is All You Need",
        "paper_year": 2017,
        "authors": ["Vaswani, A.", "Shazeer, N.", "Parmar, N.", "et al."],
        "venue": "NeurIPS",
        "arxiv_id": "1706.03762",
        "related_concepts": ["transformer", "multi-head-attention", "positional-encoding"],
    },
)

# Result:
# node_id: "graphify:concept:self-attention-mechanism"
# namespace: "graphify"
# node_type: NodeType.CONCEPT
# authority: Authority.AGENT_OBSERVATION
# confidence: 0.8
# canonical: {type: SOURCE_SPAN, ref: "papers/attention-is-all-you-need.pdf"}
# attributes: {paper_title: "Attention Is All You Need", paper_year: 2017, ...}


# ============================================================================
# Example 3: AgentMemory - Decision Node
# ============================================================================

example_agentmemory_decision = from_agentmemory(
    observation_type="decision",
    content="Use SQLite with recursive CTEs for graph traversal instead of Neo4j",
    session_id="session-kg-architecture-2024-07-26",
    timestamp=datetime(2024, 7, 26, 14, 32),
    confidence=0.9,
    metadata={
        "reasoning": "Local-first requirement, zero external dependencies, proven codebase-memory-mcp pattern",
        "alternatives_considered": ["Neo4j", "Kuzu", "LanceDB+SQLite"],
        "decision_maker": "architect-lead",
        "impact": "high",
        "reversible": False,
    },
)

# Result:
# node_id: "agentmemory:decision:use-sqlite-with-recursive-ctes"
# namespace: "session-kg-architecture-2024-07-26"
# node_type: NodeType.DECISION
# authority: Authority.AGENT_OBSERVATION
# confidence: 0.9
# canonical: {type: AGENT_MEMORY_OBSERVATION, ref: "session:session-kg-architecture-2024-07-26"}
# attributes: {reasoning: "...", alternatives_considered: [...], ...}


# ============================================================================
# Example 4: KG Native - Person Node (Wiki-synced)
# ============================================================================

example_kg_person = from_kg_native(
    node_type="person",
    name="Demis Hassabis",
    summary="British AI researcher, co-founder and CEO of DeepMind, Nobel laureate in Chemistry 2024 for AlphaFold protein structure prediction",
    wiki_path="wiki/entities/demis-hassabis.md",
    attributes={
        "affiliation": "Google DeepMind",
        "birth_year": 1976,
        "nationality": "British",
        "awards": ["Nobel Prize in Chemistry (2024)", "Knighthood (2023)", "Royal Society Fellow"],
        "education": ["PhD Cognitive Neuroscience, UCL", "MSc Computer Science, UCL"],
        "notable_contributions": ["AlphaGo", "AlphaFold", "AlphaStar"],
    },
    sources=[
        {"doc": "raw/2024-07-26--wikipedia--demis-hassabis.md", "chunk": 1},
        {"doc": "raw/2024-07-25--nobel-prize-announcement.md", "chunk": 3},
    ],
)

# Result:
# node_id: "kg:person:demis-hassabis"
# namespace: "kg"
# node_type: NodeType.PERSON
# authority: Authority.REVIEWED_WIKI
# confidence: 0.9
# canonical: {type: WIKI_SPAN, ref: "wiki/entities/demis-hassabis.md", wiki_path: "wiki/entities/demis-hassabis.md"}
# attributes: {affiliation: "Google DeepMind", awards: [...], ...}


# ============================================================================
# Example 5: KG Native - Fact Node (Temporal)
# ============================================================================

example_kg_temporal_fact = from_kg_native(
    node_type="fact",
    name="KG v0.1 uses SQLite backend",
    summary="Initial release of unified memory layer defaults to SQLite with FTS5 and sqlite-vec for vector search",
    wiki_path=None,
    attributes={
        "subject": "KG unified memory layer",
        "predicate": "uses_backend",
        "object": "SQLite",
        "version": "v0.1",
        "release_date": "2024-07-26",
        "justification": "Local-first, zero-dependency, proven codebase-memory-mcp pattern",
    },
    sources=[
        {"doc": "raw/2024-07-26--design-doc--unified-memory.md", "chunk": 7},
    ],
)
example_kg_temporal_fact.valid_from = datetime(2024, 7, 26)
example_kg_temporal_fact.valid_to = None  # Still true

# Result:
# node_id: "kg:fact:kg-v01-uses-sqlite-backend"
# namespace: "kg"
# node_type: NodeType.FACT
# authority: Authority.AGENT_OBSERVATION
# confidence: 1.0
# valid_from: datetime(2024, 7, 26)
# valid_to: None
# canonical: {type: NONE, ref: ""}
# attributes: {subject: "KG unified memory layer", predicate: "uses_backend", ...}


# ============================================================================
# Example 6: Codebase-memory-mcp - Route Node
# ============================================================================

example_codebase_route = from_codebase_memory_mcp(
    qualified_name="my-api:src/routes/users.ts#POST_/api/users",
    node_type="route",
    file_path="src/routes/users.ts",
    start_line=15,
    end_line=32,
    summary="POST endpoint to create a new user account",
    content="""router.post('/api/users', async (req, res) => {
  const { email, password } = req.body;
  const user = await userService.create({ email, password });
  res.status(201).json(user);
});""",
    attributes={
        "method": "POST",
        "path": "/api/users",
        "auth_required": False,
        "rate_limit": "10/min",
        "response_codes": [201, 400, 409],
    },
)

# Result:
# node_id: "codebase-memory-mcp:route:post_-api-users"
# namespace: "my-api"
# node_type: NodeType.ROUTE
# authority: Authority.DERIVED_DETERMINISTIC
# confidence: 1.0
# canonical: {type: SOURCE_SPAN, ref: "src/routes/users.ts:15-32", file_path: "src/routes/users.ts", ...}
# attributes: {method: "POST", path: "/api/users", ...}


# ============================================================================
# Cross-System Query Example
# ============================================================================

"""
All nodes above share the same envelope structure, enabling cross-system queries:

1. Find all decisions that affect specific code functions:
   MATCH (d:NodeEnvelope {node_type: DECISION})
   MATCH (f:NodeEnvelope {node_type: FUNCTION})
   WHERE d.content CONTAINS f.title OR d.attributes.alternatives_considered CONTAINS f.title
   RETURN d, f

2. Find all concepts referenced in specific functions:
   MATCH (f:NodeEnvelope {node_type: FUNCTION})
   MATCH (c:NodeEnvelope {node_type: CONCEPT})
   WHERE f.summary CONTAINS c.title OR f.description CONTAINS c.title
   RETURN f.title as function, c.title as concept

3. Find people involved in decisions:
   MATCH (p:NodeEnvelope {node_type: PERSON})
   MATCH (d:NodeEnvelope {node_type: DECISION})
   WHERE d.attributes.decision_maker = p.name OR d.description CONTAINS p.name
   RETURN p.name as person, d.title as decision

4. Find temporal facts valid during a specific time:
   MATCH (f:NodeEnvelope {node_type: FACT})
   WHERE f.valid_from <= '2024-08-01' AND (f.valid_to >= '2024-08-01' OR f.valid_to IS NULL)
   RETURN f.title, f.attributes

All queries work across nodes from codebase-memory-mcp, Graphify, AgentMemory,
and KG native systems because they all share the unified envelope format.
"""


# ============================================================================
# Conflict Resolution Examples
# ============================================================================

def example_conflict_resolution():
    """
    Example: Conflicting facts from AgentMemory (high confidence) vs
    Graphify (lower confidence) about the same concept.
    """

    # AgentMemory observation (session-based, confidence 0.8)
    obs_fact = from_agentmemory(
        observation_type="observation",
        content="SQLite performance degrades past 100k edges due to recursive CTE traversal",
        session_id="session-bench-2024-07-26",
        timestamp=datetime(2024, 7, 26, 16, 20),
        confidence=0.8,
        metadata={"source": "internal_benchmark", "edge_count": 100000},
    )

    # Graphify concept from paper (lower confidence, general claim)
    paper_claim = from_graphify(
        node_type="fact",
        title="SQLite handles 1M+ edges efficiently",
        description="SQLite can handle graph queries with over 1 million edges using recursive CTEs",
        source_path="papers/sqlite-graph-benchmarks.pdf",
        metadata={"source": "external_paper", "confidence": "medium"},
    )

    """
    Conflict Resolution Strategy:
    1. Both nodes are ingested (no deletion)
    2. Create a CONFLICT edge between them (pending review)
    3. Mark both with status="pending_review"
    4. kg dream pipeline evaluates:
       - AgentMemory has specific benchmark data (confidence 0.8)
       - Graphify has general claim (confidence 0.5)
    5. Resolution: Keep AgentMemory node, flag Graphify node as superseded
    6. Create EDGE: paper_claim -[superseded_by]-> obs_fact
    7. Update paper_claim.status = "tombstoned", merged_into = obs_fact.node_id
    """

    return {
        "conflict": "Performance claims disagree",
        "resolution": "Prefer specific benchmark over general paper claim",
        "winner": obs_fact.node_id,
        "loser": paper_claim.node_id,
        "reason": "higher confidence + specific measurement vs general claim",
    }


def example_merge_candidates():
    """
    Example: Same entity from different sources should merge.
    """

    # KG person node (wiki-synced)
    person_kg = from_kg_native(
        node_type="person",
        name="Geoffrey Hinton",
        summary="Godfather of AI, Nobel laureate 2024",
        wiki_path="wiki/entities/geoffrey-hinton.md",
        attributes={"affiliation": "University of Toronto", "awards": ["Nobel", "Turing"]},
        sources=[{"doc": "raw/2024-10-10--nobel.md", "chunk": 2}],
    )

    # Graphify concept from paper (citing Hinton)
    person_graphify = from_graphify(
        node_type="person",
        title="Geoffrey E. Hinton",
        description="Pioneer of deep learning, backpropagation",
        source_path="papers/backprop-1986.pdf",
        metadata={"citation_count": 50000, "institution": "University of Toronto"},
    )

    """
    Merge Strategy:
    1. Compute similarity:
       - Name similarity: 0.95 (Hinton vs Geoffrey E. Hinton)
       - Affiliation match: Toronto (exact)
       - Combined score: 0.93
    2. Since 0.93 >= 0.85 threshold, flag as merge candidate
    3. Create EDGE: person_graphify -[same_as {status: pending, confidence: 0.93}]-> person_kg
    4. kg dream pipeline reviews:
       - Both agree on affiliation
       - Different detail levels (summary vs description)
       - Combine attributes: merge awards, keep richer description
    5. Confirmed merge:
       - Keep person_kg (REVIEWED_WIKI authority > AGENT_OBSERVATION)
       - Update person_kg.attributes with additional metadata from person_graphify
       - Tombstone person_graphify with merged_into = person_kg.node_id
    """

    return {
        "candidate_merge": True,
        "similarity_score": 0.93,
        "reason": "name match + affiliation match + complementary attributes",
        "winner": person_kg.node_id,
        "loser": person_graphify.node_id,
    }


# ============================================================================
# Export all examples
# ============================================================================

__all__ = [
    "example_codebase_node",
    "example_graphify_concept",
    "example_agentmemory_decision",
    "example_kg_person",
    "example_kg_temporal_fact",
    "example_codebase_route",
    "example_conflict_resolution",
    "example_merge_candidates",
]
