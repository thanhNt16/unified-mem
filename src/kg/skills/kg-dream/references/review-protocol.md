# Review Protocol

Decision tree per candidate kind.

## recent-pair

Two same-type active nodes have canonical names scoring >= 0.85 fuzzy similarity, and neither is merged into the other.

1. Inspect the two nodes (names, summaries, aliases, sources).
2. If they refer to the same real-world entity -> `kg merge <winner> <loser> --reason "<why>"`.
3. If they are distinct but related -> keep (no action).
4. If unsure -> escalate to the user.

Do NOT auto-merge. The engine can only flag; the harness decides.

## pending

A `same_as` edge with `status='pending'` -- the gate created this during `kg save` when the dedup score fell in the gray zone (>= 0.85, < 0.95).

1. Read the edge and its two endpoint nodes.
2. If they ARE the same entity -> `kg review confirm <EDGE_ID> --winner <NODE_ID> --reason "<why>"`.
3. If they are DISTINCT -> `kg review reject <EDGE_ID> --reason "<why>"`.
4. If unsure -> escalate.

`--winner` MUST be one of the edge's two endpoint node IDs. The CLI derives the loser automatically. The confirm runs atomically: winner enriched, edges repointed, loser tombstoned, pending edge deleted -- all in one transaction.

## expiring

An active `fact` or `preference` node whose `valid_until` is in the past.

1. Check if the claim is still accurate.
2. If stale and a newer version exists -> `kg merge <current_version> <stale_version>`.
3. If still valid -> update `valid_until` on the node directly (not via dream commands; dream is read-only except for verdicts).
4. If unsure -> escalate.

ponytail: the engine has no explicit `kg expire` command yet; tombstoning via merge into a refreshed node is the current mechanism. Add a dedicated expire command in M3 if pattern repeats.

## orphan

An active node with zero `sources` entries -- it was created but never traced back to an original document.

1. If the node is genuinely sourceless (e.g. a manual add) and the user wants to keep it -> leave it; orphans are not errors, they are signal.
2. If the source was lost in extraction -> re-extract from the original `raw/` document.
3. If the node is stale or wrong -> `kg merge` into a sourced duplicate, or leave for the user.

Do NOT delete orphans automatically. They often mark legitimately hand-added context.

## contradict

Two active `fact` nodes share the same `name` (subject+predicate) but have different `summary` (object).

1. Read both fact summaries and their sources.
2. If one is wrong -> surface to the user; do not silently pick a winner. Facts are claims, not entities -- merging loses the disagreement.
3. If one supersedes the other (e.g. temporal) -> `kg merge <newer> <older>` with `--reason "superseded by <evidence>"`.
4. If they genuinely coexist (e.g. contradictory sources) -> keep both and let the user resolve in `raw/` or via explicit edit.

contradict is the highest-escalation kind. Default to escalate unless the user has already given a verdict.

## Worked example

After a fresh extract:

```
$ kg dream candidates --kind pending --json
[
  {"reason": "pending", "node_ids": ["u:person:a", "u:person:b"], "edge_id": "u:person:a|same_as|u:person:b", "score": 0.89, "detail": "gray-zone same_as", "node_type": "person"}
]

$ kg review confirm u:person:a|same_as|u:person:b --winner u:person:a --reason "same person, different surface forms"
confirmed u:person:a|same_as|u:person:b; merged u:person:b into u:person:a

$ kg wiki sync
$ kg snapshot
```

If instead the two are distinct:

```
$ kg review reject u:person:a|same_as|u:person:b --reason "different people, same name"
rejected u:person:a|same_as|u:person:b
```

No wiki/snapshot needed after a pure reject (no graph shape change beyond the edge status flip).
