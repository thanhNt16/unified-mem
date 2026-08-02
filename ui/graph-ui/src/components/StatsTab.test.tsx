/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StatsTab, IndexProgress } from "./StatsTab";
import { messages } from "../lib/i18n";
import type { CapabilitySet } from "../lib/kgAdapter";

function mockProjectsFetch(extra?: (url: string, init?: RequestInit) => Response | undefined) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const overridden = extra?.(url, init);
    if (overridden) return overridden;
    if (url === "/rpc") {
      return new Response(JSON.stringify({
        result: { content: [{ text: JSON.stringify({ projects: [] }) }] },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.startsWith("/api/ui-config")) {
      return new Response(JSON.stringify({ lang: "en" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.startsWith("/api/browse")) {
      return new Response(JSON.stringify({
        path: "/home/dev",
        parent: "/home",
        dirs: ["alpha", "beta"],
        roots: ["/", "D:/"],
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url === "/api/index") {
      return new Response(JSON.stringify({ status: "indexing", slot: 0 }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("StatsTab index modal", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("submits a custom path and project name", async () => {
    let submitted: unknown = null;
    mockProjectsFetch((url, init) => {
      if (url === "/api/index") {
        submitted = JSON.parse(String(init?.body));
        return new Response(JSON.stringify({ status: "indexing", slot: 0 }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        });
      }
      return undefined;
    });

    render(<StatsTab onSelectProject={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Index your first repository" }));

    fireEvent.change(await screen.findByLabelText("Repository path"), {
      target: { value: "D:\\work\\信租风控通后端" },
    });
    fireEvent.change(screen.getByLabelText("Project ID (optional — permanent, cannot be renamed)"), {
      target: { value: "信租风控通后端" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Index This Folder" }));

    await waitFor(() => {
      expect(submitted).toEqual({
        root_path: "D:\\work\\信租风控通后端",
        project_name: "信租风控通后端",
      });
    });
  });
});

describe("StatsTab capability gates", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("keeps indexing while omitting unsupported ADR and deletion controls", async () => {
    const capabilities: CapabilitySet = {
      graph: true,
      projects: true,
      control: false,
      index: true,
      code_view: false,
      adr: false,
      dead_code: false,
      missed_graph: false,
    };
    mockProjectsFetch((url, init) => {
      if (url === "/rpc") {
        const body = JSON.parse(String(init?.body));
        const result = body.params?.name === "list_projects"
          ? { projects: [{ name: "demo", root_path: "/repo", indexed_at: "2026-01-01T00:00:00Z" }] }
          : { node_labels: [], edge_types: [], total_nodes: 0, total_edges: 0 };
        return new Response(JSON.stringify({ result: { content: [{ text: JSON.stringify(result) }] } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return undefined;
    });

    render(<StatsTab onSelectProject={() => {}} capabilities={capabilities} />);

    /* The panel still loads and lists the project (view graph is allowed). */
    expect(await screen.findByText("demo")).toBeInTheDocument();

    /* Mutation affordances are absent, not disabled. */
    expect(screen.queryByRole("button", { name: "Index your first repository" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /\+ New Index/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "ADR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "✕" })).not.toBeInTheDocument();
  });
});

describe("IndexProgress", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("polls and shows indexing in progress when active", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve({
        json: () => Promise.resolve([
          { slot: 1, status: "indexing", path: "/path/to/project1" }
        ])
      } as unknown as Response)
    );
    vi.stubGlobal("fetch", fetchMock);

    const onDone = vi.fn();
    render(<IndexProgress onDone={onDone} />);

    // Fast-forward initial poll
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/index-status");
    expect(screen.getByText(messages.en.projects.indexingInProgress)).toBeInTheDocument();
    expect(screen.getByText("/path/to/project1")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("stops polling and calls onDone when indexing finishes successfully", async () => {
    // Backend keeps finished jobs listed with status "done" (src/ui/http_server.c
    // handle_index_status only skips idle slots) — success is a "done" entry,
    // not an empty list.
    let mockData = [
      { slot: 1, status: "indexing", path: "/path/to/project" }
    ];
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve({
        json: () => Promise.resolve(mockData)
      } as unknown as Response)
    );
    vi.stubGlobal("fetch", fetchMock);

    const onDone = vi.fn();
    render(<IndexProgress onDone={onDone} />);

    // First poll returns active
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(onDone).not.toHaveBeenCalled();

    // Indexing finishes
    mockData = [
      { slot: 1, status: "done", path: "/path/to/project" }
    ];

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(onDone).toHaveBeenCalled();
  });

  it("keeps waiting and does NOT call onDone while the jobs list is empty", async () => {
    // An empty list mid-index means the job is not visible (e.g. transient
    // backend state loss) — it must NOT be treated as successful completion.
    let mockData: { slot: number; status: string; path: string }[] = [];
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve({
        json: () => Promise.resolve(mockData)
      } as unknown as Response)
    );
    vi.stubGlobal("fetch", fetchMock);

    const onDone = vi.fn();
    render(<IndexProgress onDone={onDone} />);

    // Two empty polls: still waiting, no premature completion
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(onDone).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // Job becomes visible and finishes — now completion fires
    mockData = [{ slot: 1, status: "done", path: "/path/to/project" }];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(onDone).toHaveBeenCalled();
  });

  it("renders error banner and does NOT call onDone when indexing fails with error status", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve({
        json: () => Promise.resolve([
          { slot: 1, status: "error", path: "/path/to/failed-project", error: "OOM Error" }
        ])
      } as unknown as Response)
    );
    vi.stubGlobal("fetch", fetchMock);

    const onDone = vi.fn();
    render(<IndexProgress onDone={onDone} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    // Error banner should show up
    expect(screen.getByText(messages.en.projects.indexingFailed)).toBeInTheDocument();
    expect(screen.getByText("/path/to/failed-project")).toBeInTheDocument();
    expect(screen.getByText("OOM Error")).toBeInTheDocument();

    // onDone should not be called automatically
    expect(onDone).not.toHaveBeenCalled();

    // Click Dismiss button
    const dismissBtn = screen.getByRole("button", { name: messages.en.common.dismiss });
    expect(dismissBtn).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(dismissBtn);
    });

    // onDone should be called after manual dismissal
    expect(onDone).toHaveBeenCalled();
  });
});
