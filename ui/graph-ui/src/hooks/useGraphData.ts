import { useCallback, useState } from "react";
import type { GraphData } from "../lib/types";
import { DEFAULT_CAPABILITIES, graphUrl, type RuntimeConfig } from "../lib/kgAdapter";

export interface LoadProgress {
  receivedBytes: number;
  totalBytes: number | null;
}

interface UseGraphDataResult {
  data: GraphData | null;
  loading: boolean;
  error: string | null;
  progress: LoadProgress;
  fetchOverview: (
    project: string,
    maxNodes?: number,
    graph?: "code" | "missed",
  ) => void;
  fetchDetail: (project: string, centerNode: string) => void;
}

/* Fail-closed transport for callers without an explicit runtime. */
const LIVE_RUNTIME: RuntimeConfig = {
  mode: "live",
  capabilities: { ...DEFAULT_CAPABILITIES },
};

/* Node budget: how many nodes the layout endpoint is asked for. The default
 * keeps first paint fast; the user can raise it in 5k steps up to the hard
 * ceiling (mirrors HARD_MAX_NODES in src/ui/layout3d.c). Edges always follow
 * the budget — the server returns every edge between the loaded nodes. */
export const GRAPH_RENDER_NODE_LIMIT = 2000;
export const GRAPH_NODE_BUDGET_STEP = 500;
export const GRAPH_NODE_BUDGET_MAX = 2000;

export function clampNodeBudget(value: number): number {
  if (!Number.isFinite(value)) return GRAPH_RENDER_NODE_LIMIT;
  const stepped =
    Math.round(value / GRAPH_NODE_BUDGET_STEP) * GRAPH_NODE_BUDGET_STEP;
  if (stepped < GRAPH_NODE_BUDGET_STEP) return GRAPH_NODE_BUDGET_STEP;
  if (stepped > GRAPH_NODE_BUDGET_MAX) return GRAPH_NODE_BUDGET_MAX;
  return stepped;
}

/** Which graph to lay out: the code graph (default) or the missed graph —
 *  only files the indexer could not fully cover, as their file structure. */
export type GraphVariant = "code" | "missed";

export async function fetchLayout(
  project: string,
  maxNodes = GRAPH_RENDER_NODE_LIMIT,
  onProgress?: (progress: LoadProgress) => void,
  graph: GraphVariant = "code",
  runtime: RuntimeConfig = LIVE_RUNTIME,
): Promise<GraphData> {
  /* Route every layout fetch through the adapter: static deployments read the
   * packaged snapshot; live deployments hit /api/layout with the adapter's
   * clamped 1..2000 budget. The missed-graph variant is a live-only query. */
  const base = graphUrl(runtime, project, maxNodes);
  const url =
    graph === "missed" && runtime.mode === "live" ? `${base}&graph=missed` : base;
  const res = await fetch(url);

  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error ?? `HTTP ${res.status}`);
  }

  /* Stream the body when possible so large budgets show live download
   * progress instead of a silent stall. */
  if (!res.body || !onProgress) {
    return res.json();
  }

  const lengthHeader = res.headers.get("content-length");
  const totalBytes = lengthHeader ? parseInt(lengthHeader, 10) || null : null;
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let receivedBytes = 0;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    receivedBytes += value.length;
    onProgress({ receivedBytes, totalBytes });
  }

  const merged = new Uint8Array(receivedBytes);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return JSON.parse(new TextDecoder().decode(merged));
}

const NO_PROGRESS: LoadProgress = { receivedBytes: 0, totalBytes: null };

export function useGraphData(runtime: RuntimeConfig = LIVE_RUNTIME): UseGraphDataResult {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<LoadProgress>(NO_PROGRESS);

  const fetchOverview = useCallback(
    async (project: string, maxNodes?: number, graph: GraphVariant = "code") => {
      setLoading(true);
      setError(null);
      setProgress(NO_PROGRESS);
      try {
        const result = await fetchLayout(project, maxNodes, setProgress, graph, runtime);
        setData(result);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to fetch layout");
      } finally {
        setLoading(false);
      }
    },
    [runtime],
  );

  const fetchDetail = useCallback(
    async (project: string, _centerNode: string) => {
      setLoading(true);
      setError(null);
      setProgress(NO_PROGRESS);
      try {
        /* TODO: detail level with center_node filtering */
        const result = await fetchLayout(project, undefined, setProgress, "code", runtime);
        setData(result);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to fetch layout");
      } finally {
        setLoading(false);
      }
    },
    [runtime],
  );

  return { data, loading, error, progress, fetchOverview, fetchDetail };
}
