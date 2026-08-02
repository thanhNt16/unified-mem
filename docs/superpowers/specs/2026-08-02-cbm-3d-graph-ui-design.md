# Pinned CBM 3D Graph UI Design

**Date:** 2026-08-02
**Status:** Approved
**Upstream:** [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp), commit `d6be58e`

## Goal

Use the exact built-in Codebase Memory 3D graph shell for both the live `kg viz` command and the static GitHub Pages demo. Maintain one frontend source and one 3D layout implementation. Preserve truthful kg capabilities; hide unsupported CBM-only controls.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Surfaces | Live `kg viz` and static Pages | One UI across both entry points |
| UI scope | Full CBM shell | Exact visual and interaction target |
| Upstream version | Commit `d6be58e` | Reproducible parity and intentional syncs |
| Adoption method | Vendored fork | Verbatim source, auditable local patches |
| Layout | Server-side Python port of CBM `layout3d.c` | Matching geometry without a C server dependency |
| Live behavior | Functional kg equivalents | No decorative controls |
| Unsupported controls | Hidden | Preserve an honest capability boundary |
| Static data | Precomputed bundled snapshot | No backend or upload flow required |
| Runtime dependencies | No Node or CDN | Self-contained wheel and Pages build |

## Architecture

Vendor upstream `graph-ui/` into `ui/graph-ui/`, retaining its source organization, tests, dependency versions, MIT license, and attribution. Record the upstream repository, pinned commit, copied paths, and local patches in a provenance manifest.

The vendored frontend is the only graph frontend. Its production build serves two targets:

- **Live:** bundled into the Python wheel and served by `kg viz` through the existing loopback HTTP server.
- **Static:** deployed unchanged to GitHub Pages and configured to read a precomputed snapshot.

Replace the inline 2D SVG currently served by `src/kg/viz/server.py`. Retire the duplicated React source under `docs/demo/src/`; retain only dataset or snapshot-generation inputs that remain useful.

The Python backend implements CBM-compatible API responses and a Python port of the relevant `layout3d.c` behavior. It does not port or embed CBM's C HTTP server.

## Components

### Vendored frontend

Preserve upstream components unchanged where possible, including the graph scene, node cloud, edges, labels, tooltip, tabs, sidebar, filters, project cards, display controls, loader, and error states.

One narrow kg adapter owns all intentional divergence:

- select `live` or `static` transport;
- read the kg capability response;
- hide unsupported features;
- preserve original kg node identifiers alongside integer render identifiers;
- avoid changes throughout upstream presentation components.

No separate Pages application exists.

### Backend API

Keep `src/kg/viz/server.py` as the local HTTP host. Extract two focused units:

- `src/kg/viz/layout3d.py`: deterministic CBM-compatible 3D layout;
- `src/kg/viz/api.py`: CBM-shaped serialization and endpoint handling.

Existing kg graph loading and clustering remain authoritative. The live server exposes the pinned frontend's required contracts, including:

- `/api/layout` for positioned graph data;
- `/api/repo-info` for project metadata;
- `/api/index` for supported local indexing controls;
- a capability response used to conditionally expose shell features.

Exact route naming may follow the pinned upstream contract where it already defines an equivalent. The adapter must not invent duplicate transport contracts.

### Packaging

Include the built frontend assets as Python package data and serve them with `importlib.resources`. Node is a development and release-build dependency only. An installed wheel must run `kg viz` without Node, a package manager, CDN access, or external assets.

### Static snapshot

A build script invokes the same Python graph normalization and layout pipeline used by the live server. It writes the CBM-shaped layout payload beside the frontend assets. Static mode reads that file and reports capabilities that hide mutation-oriented Projects and Control features unavailable on Pages.

## Data Contract and Flow

### Internal normalization

Normalize kg nodes and edges into one layout input. Sort nodes stably before assigning integer render IDs. Preserve the original string kg ID in a separate field used for selection and detail lookup.

The layout output follows the pinned CBM shape required by the vendored frontend: positioned nodes with integer IDs, `x/y/z`, display metadata, size, color, status where meaningful, and edges using render IDs. Fields without valid kg semantics are omitted or mapped explicitly by the adapter; fabricated dead-code or call-analysis states are forbidden.

### Live flow

1. `kg viz` loads the local graph through the existing kg reader.
2. The backend normalizes and validates the graph.
3. `layout3d.py` computes node positions, sizes, colors, call-depth layering where applicable, and overview/detail levels using fixed constants and seed behavior derived from upstream `layout3d.c`.
4. `api.py` serializes the result into the pinned CBM API contract.
5. The vendored UI renders the response.
6. Supported Projects and Control actions call kg-backed endpoints.

### Static flow

1. The snapshot build loads the committed demo dataset.
2. The same normalization and `layout3d.py` pipeline computes geometry.
3. The generated API payload ships with the frontend build.
4. Static transport reads the payload instead of mutation endpoints.
5. Static capabilities hide Projects and Control actions that require a live backend.

### Determinism

Stable input ordering, fixed layout constants, and fixed seed behavior must make repeated layout runs byte-stable after excluding explicit generation metadata. The same dataset must produce matching geometry in live and static modes.

## Capability Boundary

Expose only capabilities backed by kg behavior. Hide unsupported CBM-only controls rather than displaying disabled or decorative UI. This includes ADR operations, dead-code analysis, missed-call indexing, and any project-management action without a real kg equivalent.

Unknown or malformed capability responses default to hiding optional controls. Static mode never exposes mutation controls.

## Error Handling and Security

- Reject an invalid or missing graph before starting the live server; do not expose partial state.
- Retain existing limits of 2,000 nodes, 4,000 edges, and a 4 MiB payload.
- Report truncation in the API response. Show the upstream missed-graph callout only when its semantics accurately describe the truncation.
- Return structured JSON errors from API endpoints.
- Preserve the last valid graph when a layout refresh fails; show the pinned loader/error state with retry.
- Permit one indexing operation at a time. Return explicit progress and failure states.
- Validate project paths at the API trust boundary.
- Bind only to loopback by default.
- Retain CSP and path-traversal protections.
- Load no runtime frontend code or assets from external networks.
- In static mode, show the pinned loader error when the bundled snapshot is missing or malformed.

## Upstream Maintenance

The provenance manifest is the synchronization boundary. It records:

- upstream repository and commit;
- vendored paths;
- upstream license;
- every local patch and its purpose.

An upstream refresh is an explicit change: update the pin, reapply the documented adapter patches, run upstream and local tests, inspect the frontend diff, and regenerate the static snapshot. Automatic tracking of upstream `main` is out of scope.

## Testing

### Backend

- deterministic fixture proving identical input yields identical IDs and positions;
- contract test for the pinned `/api/layout` shape;
- endpoint tests for success, truncation, malformed requests, indexing lock, and rejected paths;
- equivalence test proving live and static serializers produce the same graph payload;
- package test proving built assets are present and readable through `importlib.resources`.

### Frontend

- retain the pinned upstream test suite;
- add only adapter tests for live/static transport and capability hiding;
- build both modes from the same source;
- verify no runtime network dependency.

### Browser smoke check

At a desktop viewport, verify:

- full CBM shell renders;
- graph positions and interactions work;
- filters, labels, selection, tooltip, and display controls work;
- live Projects and Control features call real kg endpoints;
- unsupported controls are absent;
- static mode loads bundled data and exposes no mutation controls.

A local pixel-snapshot suite is unnecessary: the pinned vendored source is the UI identity mechanism.

## Acceptance Criteria

1. `kg viz` displays the full pinned CBM shell and server-positioned 3D graph.
2. GitHub Pages displays the same frontend build with bundled snapshot data.
3. Identical graph input produces matching live and static geometry.
4. Supported Projects and Control actions operate against kg endpoints.
5. Unsupported CBM-only controls are absent.
6. The installed wheel includes all UI assets and requires no Node or network access at runtime.
7. Existing graph size, payload, loopback, CSP, and path protections remain effective.
8. Repository metadata preserves MIT attribution, upstream commit, and local patch provenance.

## Explicit Non-Goals

- Tracking upstream `main` automatically.
- Porting CBM's C HTTP server.
- Adding dead-code, ADR, missed-call, or other CBM analysis semantics to kg.
- Maintaining the existing 2D SVG or a second React graph implementation.
- Adding runtime graph upload to the static Pages demo.
- Building a monorepo or shared-package abstraction for a single frontend.
