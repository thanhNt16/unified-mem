/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

/* GraphScene renders a WebGL <Canvas> which jsdom can't run — stub it out. */
vi.mock("./components/GraphScene", () => ({
  GraphScene: () => null,
  computeCameraTarget: () => null,
}));

afterEach(() => vi.unstubAllGlobals());

describe("App static fallback", () => {
  it("reaches the graph snapshot on a static bare root when both transports fail", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "./graph-snapshot.json") {
        return new Response(JSON.stringify({ nodes: [], edges: [], total_nodes: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      /* /api/capabilities AND ./capabilities.json both absent. */
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    /* Both transports are probed in order… */
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/capabilities"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("./capabilities.json"));

    /* …the app fails closed to static and serves the snapshot instead of
     * hanging on the loading screen. */
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("./graph-snapshot.json"));
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    expect(screen.getByText("Graph")).toBeInTheDocument();
  });
});
