# /kg:* Data Flow Diagrams

## Full Pipeline

```mermaid
flowchart TD
    subgraph Ingest["/kg:ingest"]
        Source[Raw Source] --> Fingerprint[SHA256 Fingerprint]
        Fingerprint --> Registry{Already<br/>Registered?}
        Registry -->|No| Normalize[Format<br/>Normalization]
        Registry -->|Yes| Skip[Skip with Status]
        Normalize --> Chunk[Chunk Boundaries<br/>512 tok / 64 overlap]
        Chunk --> RawFile[.kg/raw/<type>/<sha256>.md]
        RawFile --> RegWrite[Registry Write<br/>.kg/registry/index.jsonl]
    end

    subgraph Extract["/kg:extract"]
        RegWrite --> ChunkLoop[Per-Chunk Loop]
        ChunkLoop --> LoadChunk[Load Chunk Text]
        LoadChunk --> LLM[Harness LLM<br/>Extraction]
        LLM --> Validate[Schema Validation]
        Validate -->|Pass| Gate[kg save Gate<br/>validate→resolve→embed→dedup]
        Validate -->|Fail x2| CheckpointFailed[Checkpoint Failed]
        Gate -->|FLAGGED| ReviewQueue[Pending Edges]
        Gate -->|NEW/RESOLVED/MERGED| Graph[kg.db<br/>nodes + edges]
        Gate --> CheckpointDone[Checkpoint Done]
    end

    subgraph Query["/kg:query"]
        Graph --> ModeSelect{Mode Selection}
        ModeSelect -->|Hybrid 90%| Search[RRF Search<br/>BM25 + ANN]
        ModeSelect -->|NL-Cypher| Cypher[Generate +<br/>Execute Cypher]
        ModeSelect -->|Deep-search| WikiBuild[Materialize Wiki]
        Search --> Expand[BFS Expand<br/>hops=2]
        Expand --> Pack[Rank +<br/>Trim to Budget]
        Pack --> Answer[Model Answer<br/>with Citations]
        Cypher --> Tabular[Tabular Results]
        WikiBuild --> Progressive[Progressive Disclosure]
    end

    subgraph Dream["/kg:dream"]
        ReviewQueue --> Discovery[Candidate Discovery]
        Graph --> Discovery
        Discovery --> VerdictLoop[Verdict Loop]
        VerdictLoop --> Confirm[kg review confirm<br/>winner enriches]
        VerdictLoop --> Reject[kg review reject<br/>edge deleted]
        VerdictLoop --> Merge[kg merge<br/>manual merge]
        Confirm --> AuditLog[wiki/log.md<br/>Audit Trail]
        Reject --> AuditLog
        Merge --> AuditLog
        AuditLog --> WikiSync[kg wiki sync<br/>regen pages]
        WikiSync --> Graph
    end

    Source --> Source[External Sources:<br/>markdown, PDF, code,<br/>transcripts, agent-memory]
```

## Cross-System Integration

```mermaid
flowchart LR
    subgraph CodebaseMemory["codebase-memory-mcp"]
        CBM[Indexed Codebase]
        CBMQuery[Query Functions]
    end

    subgraph Graphify["Graphify"]
        GF[Transcripts]
        GFReindex[Re-index Mentions]
    end

    subgraph AgentMemory["AgentMemory"]
        AM[Session Observations]
        AMRecall[Recall Memory]
    end

    subgraph KG["Knowledge Graph"]
        Ingest[/kg:ingest]
        Extract[/kg:extract]
        Query[/kg:query]
        Dream[/kg:dream]
        DB[kg.db]
    end

    subgraph Wiki["wiki"]
        Pages[Entity Pages]
        Sync[kg wiki sync]
    end

    Ingest -->|Route code sources| CBMQuery
    CBM -->|Extract entities| Extract
    Extract --> DB

    Ingest -->|Route transcripts| GF
    GF -->|Extract nodes| Extract

    Ingest -->|Route agent-memory| AMRecall
    AM -->|Extract observations| Extract

    Query -->|Query functions| CBMQuery
    CBMQuery --> Query

    Query -->|Query conversations| GF
    GF --> Query

    Query -->|Query sessions| AMRecall
    AMRecall --> Query

    Dream -->|Update refs| CBMQuery
    Dream -->|Re-index| GFReindex
    Dream -->|Delete obs| AM

    DB --> Sync
    Sync --> Pages
    Pages --> Query
```

## Query Mode Routing

```mermaid
flowchart TD
    Question[User Question] --> Parse[Parse for Keywords]

    Parse --> ModeDecide{Mode Selection}

    ModeDecide -->|Structural<br/>path, all, count| NLCypher[NL-Cypher Mode]
    ModeDecide -->|Broad<br/>everything about| DeepSearch[Deep-Search Mode]
    ModeDecide -->|Default| Hybrid[Hybrid Mode]

    Hybrid --> Search[RRF Search<br/>kg search]
    Search --> Expand[BFS Expand<br/>kg expand]
    Expand --> Pack[Pack + Trim<br/>kg pack]
    Pack --> Answer[Answer with<br/>Source Citations]

    NLCypher --> Ontology[Read<br/>ontology.json]
    Ontology --> Generate[Generate<br/>Cypher]
    Generate --> ValidateCypher[Validate<br/>Read-only]
    ValidateCypher --> ExecuteCypher[Execute<br/>kg cypher]
    ExecuteCypher --> Tabular[Tabular Results]

    DeepSearch --> BuildWiki[Materialize Wiki<br/>kg wiki build]
    BuildWiki --> Index[Read index.md]
    Index --> EntityPages[Entity Pages]
    EntityPages --> RawFallback[Raw Fallback<br/>if needed]
```

## Dream Candidate Processing

```mermaid
flowchart TD
    Queue[kg dream candidates] --> Discovery[Candidate Discovery<br/>per kind]

    Discovery --> Pending[Pending same_as<br/>0.85-0.95]
    Discovery --> RecentPair[Recent-Pair<br/>high similarity]
    Discovery --> Expiring[Expiring<br/>time-limited]
    Discovery --> Orphan[Orphan<br/>isolated facts]
    Discovery --> Contradict[Contradictions<br/>conflicting claims]

    Pending --> VerdictPending{Verdict?}
    VerdictPending -->|confirm| Confirm[kg review confirm]
    VerdictPending -->|reject| Reject[kg review reject]
    VerdictPending -->|keep/escalate| KeepPending[No Action]

    RecentPair --> VerdictRecent{Verdict?}
    VerdictRecent -->|merge| Merge[kg merge]
    VerdictRecent -->|keep/escalate| KeepRecent[No Action]

    Confirm --> Audit[Write Audit Log]
    Reject --> Audit
    Merge --> Audit
    KeepPending --> Skip1[Skip]
    KeepRecent --> Skip2[Skip]

    Audit --> WikiSync[kg wiki sync]
    WikiSync --> Snapshot[kg snapshot]
    Snapshot --> UpdateGraph[Update kg.db]

    Expiring --> TimeCheck{Auto-expire?}
    TimeCheck -->|yes| AutoRemove[Remove from Queue]
    TimeCheck -->|no| ManualExpire[Manual Review Needed]

    Orphan --> Attach{Attachable?}
    Attach -->|yes| AttachEdge[Add Edges via kg save]
    Attach -->|no| DeleteOrphan[Delete or Keep]

    Contradict --> Resolve{Resolvable?}
    Resolve -->|yes| ResolveConfirm[kg review confirm<br/>correct fact]
    Resolve -->|no| FlagConflict[Flag for User]
```

## State Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Ingesting: /kg:ingest start
    Ingesting --> Registered: Registry write complete
    Registered --> Extracting: /kg:extract start
    Extracting --> Extracted: All chunks done
    Extracted --> QueryReady: In kg.db
    QueryReady --> [*]: /kg:query returns answer

    Extracting --> Flagged: Gray-zone pairs
    Flagged --> DreamQueue: Pending edges
    DreamQueue --> Reviewing: /kg:dream start
    Reviewing --> Merged: Verdict confirm/merge
    Reviewing --> Rejected: Verdict reject
    Merged --> QueryReady: Updated in kg.db
    Rejected --> [*]: Edge deleted

    Registered --> Failed: Chunk fails x2
    Failed --> [*]: Requires manual fix

    note right of Ingesting
        Evidence preserved
        in .kg/raw/
    end note

    note right of Extracting
        Per-chunk isolation
        gate normalization
    end note

    note right of QueryReady
        Read-only, cited
        answer
    end note

    note right of DreamQueue
        Human verdict
        no auto-merge
    end note
```

## Command Call Graph

```mermaid
flowchart TD
    CLI[User CLI] --> Ingest[kg ingest]
    CLI --> Extract[kg extract]
    CLI --> Query[kg query / search / expand / pack]
    CLI --> Dream[kg dream candidates / merge / review]

    Ingest --> Markitdown[markitdown CLI]
    Ingest --> RegWrite[kg registry append]
    Ingest --> ChunkFrontmatter[Add frontmatter to .md]

    Extract --> RawStatus[kg raw status]
    Extract --> LLMPrompt[Harness LLM]
    Extract --> ValidateSchema[JSON schema validate]
    Extract --> Save[kg save]
    Extract --> Checkpoint[kg raw checkpoint]
    Extract --> WikiSync[kg wiki sync]

    Save --> Gate[Gate.normalize]
    Gate --> Resolve[Name resolution]
    Gate --> Embed[Embedding]
    Gate --> Dedup[Deduplication]

    Query --> Search[kg search]
    Search --> FTS5[FTS5/BM25]
    Search --> ANN[sqlite-vec ANN]
    Search --> RRF[RRF Fusion]

    Query --> Expand[kg expand]
    Expand --> BFS[Bidirectional BFS CTE]

    Query --> Pack[kg pack]
    Pack --> Rank[Rank: RRF+degree+recency]
    Pack --> Trim[Trim to budget]

    Query --> Cypher[kg cypher]
    Cypher --> ASTValidate[AST validate]
    Cypher --> ExecCypher[Execute read-only]

    Dream --> Candidates[kg dream candidates]
    Candidates --> SQLQueries[Per-kind SQL queries]

    Dream --> ReviewConfirm[kg review confirm]
    Dream --> ReviewReject[kg review reject]
    Dream --> Merge[kg merge]

    ReviewConfirm --> TxCommit[Transaction commit]
    ReviewReject --> TxCommit
    Merge --> TxCommit

    TxCommit --> AuditWrite[wiki/log.md append]
    TxCommit --> WikiRegen[kg wiki sync]
    TxCommit --> SnapshotWrite[kg snapshot]
```
