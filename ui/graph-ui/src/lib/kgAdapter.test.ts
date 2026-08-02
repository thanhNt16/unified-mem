import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_CAPABILITIES,
  graphUrl,
  loadRuntime,
} from "./kgAdapter";

afterEach(() => vi.unstubAllGlobals());

describe("kg adapter", () => {
  it("uses explicit live capabilities", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ graph: true, projects: true, control: true, index: true }),
    }));
    const runtime = await loadRuntime();
    expect(runtime.mode).toBe("live");
    expect(runtime.capabilities.adr).toBe(false);
    expect(graphUrl(runtime, "kg", 2000)).toBe("/api/layout?project=kg&max_nodes=2000");
  });

  it("falls back to static snapshot when the live endpoint is absent", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 404 })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ graph: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const runtime = await loadRuntime();
    expect(runtime.mode).toBe("static");
    expect(runtime.capabilities).toEqual(DEFAULT_CAPABILITIES);
    expect(graphUrl(runtime, "ignored", 2000)).toBe("./graph-snapshot.json");
  });

  it("fails closed for unknown capability keys", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ graph: true, adr: "yes", code_view: 1 }),
    }));
    const runtime = await loadRuntime();
    expect(runtime.capabilities.adr).toBe(false);
    expect(runtime.capabilities.code_view).toBe(false);
  });
});
