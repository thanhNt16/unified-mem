/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GraphTab } from "./GraphTab";
import type { GraphData } from "../lib/types";
import { DEFAULT_CAPABILITIES, type RuntimeConfig } from "../lib/kgAdapter";

const testRuntime: RuntimeConfig = {
  mode: "live",
  capabilities: {
    ...DEFAULT_CAPABILITIES,
    projects: true,
    index: true,
    code_view: true,
    adr: true,
    dead_code: true,
    missed_graph: true,
  },
};

/* GraphScene renders a WebGL <Canvas> which jsdom can't run — stub it out. */
vi.mock("./GraphScene", () => ({
  GraphScene: () => null,
  computeCameraTarget: () => null,
}));

const SAMPLE: GraphData = {
  nodes: [
    {
      id: 1, x: 0, y: 0, z: 0, label: "Function", name: "orphan",
      file_path: "src/orphan.ts", size: 1, color: "#fff", status: "dead", in_calls: 0,
    },
    {
      id: 2, x: 1, y: 0, z: 0, label: "Function", name: "used",
      file_path: "src/used.ts", size: 1, color: "#fff", status: "normal", in_calls: 3,
    },
  ],
  edges: [{ source: 2, target: 1, type: "CALLS" }],
  total_nodes: 2,
};

function mockLayoutFetch(data: GraphData) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/layout")) {
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("GraphTab dead-code filters", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the dead count and filters to only dead code on toggle", async () => {
    mockLayoutFetch(SAMPLE);
    render(<GraphTab project="demo" runtime={testRuntime} />);

    /* Panel loaded; the dead-code section reports one dead node. */
    expect(await screen.findByText("Filters")).toBeInTheDocument();
    expect(screen.getByText("1 dead")).toBeInTheDocument();

    /* Both nodes visible initially — no "filtered from" notice. */
    expect(screen.queryByText(/filtered from/)).not.toBeInTheDocument();

    /* Toggling "Show only dead code" hides the non-dead node. */
    fireEvent.click(screen.getByRole("button", { name: /Show only dead code/ }));
    expect(await screen.findByText(/filtered from 2/)).toBeInTheDocument();
  });
});

describe("GraphTab capability gates", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads the packaged snapshot on a static bare root (no project)", async () => {
    const staticRuntime: RuntimeConfig = {
      mode: "static",
      capabilities: {
        graph: true,
        projects: false,
        control: false,
        index: false,
        code_view: false,
        adr: false,
        dead_code: false,
        missed_graph: false,
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "./graph-snapshot.json") {
        return new Response(JSON.stringify(SAMPLE), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<GraphTab project={null} runtime={staticRuntime} />);

    /* The graph loader runs despite the missing project and fetches the
     * snapshot — no "Select a project" placeholder, no /api/layout call. */
    expect(await screen.findByText("Filters")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("./graph-snapshot.json");
    const layoutCalls = fetchMock.mock.calls.filter((c) =>
      String(c[0]).startsWith("/api/layout"));
    expect(layoutCalls).toHaveLength(0);
    expect(screen.queryByText(/Select a project/)).not.toBeInTheDocument();
  });

  it("omits dead-code and missed-graph controls when the runtime reports them unsupported", async () => {
    const unsupported: RuntimeConfig = {
      mode: "live",
      capabilities: {
        graph: true,
        projects: false,
        control: false,
        index: false,
        code_view: false,
        adr: false,
        dead_code: false,
        missed_graph: false,
      },
    };
    mockLayoutFetch(SAMPLE);
    render(<GraphTab project="demo" runtime={unsupported} />);

    /* Panel still loads — the graph itself is always available. */
    expect(await screen.findByText("Filters")).toBeInTheDocument();

    /* Dead-code and missed-graph sections are absent, not disabled. */
    expect(screen.queryByText("Dead code")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show only dead code/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Missed files")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show missed skeleton/ })).not.toBeInTheDocument();
  });
});
