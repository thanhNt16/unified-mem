# Codebase Memory Graph UI Provenance

- Upstream: https://github.com/DeusData/codebase-memory-mcp
- Commit: `d6be58ef9d43c574a2d1b0827ecc1e3c4846f0fe`
- Copied path: `graph-ui/`
- License: MIT; see `LICENSE.upstream`

## Local patches

Each local frontend change must be listed here with its file paths and reason.

1. `src/lib/kgAdapter.ts` and `src/lib/kgAdapter.test.ts`: select live/static kg transports and parse capabilities fail-closed.
2. `src/App.tsx`, `src/components/{GraphTab,StatsTab,ControlTab,NodeDetailPanel}.tsx`, and `src/hooks/useGraphData.ts`: route data through the adapter and hide features without kg semantics.
3. Build configuration changes listed below: produce self-contained relative assets for wheel and Pages deployment.

## Refresh procedure

1. Copy `graph-ui/` from a reviewed upstream commit.
2. Update the full commit above.
3. Reapply every patch listed in this file.
4. Run `npm test` and `npm run build` in `ui/graph-ui/`.
5. Run `make test` and inspect the vendored source diff.
