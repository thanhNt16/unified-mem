export type CapabilitySet = {
  graph: boolean;
  projects: boolean;
  control: boolean;
  index: boolean;
  code_view: boolean;
  adr: boolean;
  dead_code: boolean;
  missed_graph: boolean;
};

export type RuntimeConfig = {
  mode: "live" | "static";
  capabilities: CapabilitySet;
};

export const DEFAULT_CAPABILITIES: CapabilitySet = {
  graph: true,
  projects: false,
  control: false,
  index: false,
  code_view: false,
  adr: false,
  dead_code: false,
  missed_graph: false,
};

/* Default gate set for components rendered without an explicit runtime
 * (upstream tests and callers that predate the capability adapter). */
export const ALL_CAPABILITIES: CapabilitySet = {
  graph: true,
  projects: true,
  control: true,
  index: true,
  code_view: true,
  adr: true,
  dead_code: true,
  missed_graph: true,
};

const MAX_NODES = 2000;

/* Capability keys accepted from any transport. Unknown keys fail closed. */
const KNOWN: (keyof CapabilitySet)[] = [
  "graph",
  "projects",
  "control",
  "index",
  "code_view",
  "adr",
  "dead_code",
  "missed_graph",
];

/* Parse a capability object, keeping only literal `true` for known keys;
 * absent, `false`, or non-literal values default to `false`. */
function parseCapabilities(raw: unknown): CapabilitySet {
  const caps = { ...DEFAULT_CAPABILITIES };
  if (raw && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    for (const key of KNOWN) {
      if (obj[key] === true) caps[key] = true;
    }
  }
  return caps;
}

/* Static transports may only expose the graph — mutation capabilities
 * (projects/control/index/adr) never turn on without a live backend. */
function staticCapabilities(raw: unknown): CapabilitySet {
  const caps = { ...DEFAULT_CAPABILITIES };
  if (raw && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    if (obj.graph === true) caps.graph = true;
  }
  return caps;
}

async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function loadRuntime(): Promise<RuntimeConfig> {
  try {
    const caps = parseCapabilities(await fetchJson("/api/capabilities"));
    return { mode: "live", capabilities: caps };
  } catch {
    /* Live endpoint absent (404) or unreachable — serve the packaged snapshot. */
    const staticCaps = await fetchJson("./capabilities.json");
    return { mode: "static", capabilities: staticCapabilities(staticCaps) };
  }
}

export function graphUrl(
  runtime: RuntimeConfig,
  project: string,
  maxNodes: number,
): string {
  if (runtime.mode === "static") return "./graph-snapshot.json";
  const clamp = Math.min(MAX_NODES, Math.max(1, Math.floor(maxNodes)));
  const params = new URLSearchParams({ project, max_nodes: String(clamp) });
  return `/api/layout?${params.toString()}`;
}
