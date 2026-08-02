import { useCallback, useEffect, useState } from "react";
import { GraphTab } from "./components/GraphTab";
import { StatsTab } from "./components/StatsTab";
import type { TabId } from "./lib/types";
import { useUiMessages } from "./lib/i18n";
import { loadRuntime, DEFAULT_CAPABILITIES } from "./lib/kgAdapter";
import type { RuntimeConfig } from "./lib/kgAdapter";

const TAB_IDS: TabId[] = ["graph", "stats"];

interface RouteState {
  tab: TabId;
  project: string | null;
}

/* Read the active tab + selected project from the URL query string so the
 * current view survives refreshes and can be bookmarked or shared. */
function readRoute(): RouteState {
  const params = new URLSearchParams(window.location.search);
  const rawTab = params.get("tab");
  const tab = TAB_IDS.includes(rawTab as TabId) ? (rawTab as TabId) : "stats";
  const project = params.get("project");
  return { tab, project: project ? project : null };
}

/* Build the canonical URL for a route, preserving the path and hash. */
function routeUrl(tab: TabId, project: string | null): string {
  const params = new URLSearchParams();
  params.set("tab", tab);
  if (project) params.set("project", project);
  return `${window.location.pathname}?${params.toString()}${window.location.hash}`;
}

export function App() {
  const t = useUiMessages();
  const [route, setRoute] = useState<RouteState>(readRoute);
  const { tab: activeTab, project: selectedProject } = route;

  /* Resolve the runtime transport (live /api/capabilities or the packaged
   * snapshot) exactly once, before any tab renders. */
  const [runtime, setRuntime] = useState<RuntimeConfig | null>(null);
  useEffect(() => {
    let cancelled = false;
    /* loadRuntime never rejects on transport absence, but guard anyway so an
     * unexpected failure cannot strand the app on the loading screen. */
    loadRuntime().then(
      (r) => {
        if (!cancelled) setRuntime(r);
      },
      () => {
        if (!cancelled) {
          setRuntime({ mode: "static", capabilities: { ...DEFAULT_CAPABILITIES } });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  /* Normalize the URL on first load so it always carries the current route. */
  useEffect(() => {
    const initial = readRoute();
    window.history.replaceState(null, "", routeUrl(initial.tab, initial.project));
  }, []);

  /* Sync state when the user navigates with the browser back/forward buttons. */
  useEffect(() => {
    const onPopState = () => setRoute(readRoute());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  /* Change the route and push a history entry (skips no-op navigations). */
  const navigate = useCallback((tab: TabId, project: string | null) => {
    const url = routeUrl(tab, project);
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (url === current) return;
    window.history.pushState(null, "", url);
    setRoute({ tab, project });
  }, []);

  /* Runtime has not resolved yet — do not render capability-gated tabs.
   * Nothing is shown until loadRuntime() decides live vs static. */
  if (runtime === null) {
    return (
      <div className="h-screen flex items-center justify-center bg-background text-foreground">
        <p className="text-[13px] text-foreground/40">Loading…</p>
      </div>
    );
  }

  /* Gate the tabs on the resolved runtime capabilities. */
  const tabs: { id: TabId; label: string }[] = [{ id: "graph", label: t.tabs.graph }];
  if (runtime.capabilities.projects) tabs.push({ id: "stats", label: t.tabs.projects });

  /* A route pointing at a hidden tab falls back to the always-available graph. */
  const supported = new Set(tabs.map((tab) => tab.id));
  const effectiveTab = supported.has(activeTab) ? activeTab : "graph";

  return (
    <div className="h-screen flex flex-col bg-background text-foreground">
      {/* Header */}
      <header className="flex items-center justify-between px-5 h-12 border-b border-border bg-[#0b1920]/80 backdrop-blur-md shrink-0">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="w-[7px] h-[7px] rounded-full bg-primary" />
            <span className="text-[13px] font-semibold text-foreground/90 tracking-tight">
              Codebase Memory
            </span>
          </div>

          {/* Tabs inline in header */}
          <nav className="flex items-center gap-0.5">
            {tabs.map((tab) => {
              /* Graph is reachable without a project only when static mode
               * serves the snapshot (live mode requires a selection first). */
              const disabled =
                tab.id === "graph" && !selectedProject && runtime.mode !== "static";
              return (
                <button
                  key={tab.id}
                  onClick={() => navigate(tab.id, tab.id === "stats" ? null : selectedProject)}
                  disabled={disabled}
                  title={disabled ? "Select a project first" : undefined}
                  className={`px-3 py-1 rounded-md text-[12px] font-medium transition-all ${
                    disabled
                      ? "text-muted-foreground/30 cursor-not-allowed"
                      : effectiveTab === tab.id
                        ? "bg-primary/15 text-primary"
                        : "text-muted-foreground hover:text-foreground hover:bg-white/[0.04]"
                  }`}
                >
                  {tab.label}
                </button>
              );
            })}
          </nav>
        </div>

        {selectedProject && (
          <div className="flex items-center gap-2 px-3 py-1 rounded-lg bg-white/[0.04] border border-border/30">
            <span className="text-[10px] text-foreground/30 uppercase tracking-wider">
              {t.graph.selectedLabel}
            </span>
            <span className="text-[11px] text-primary font-mono truncate max-w-[300px]">
              {selectedProject}
            </span>
            <button
              aria-label="Clear selected project"
              onClick={() => navigate("stats", null)}
              className="text-foreground/20 hover:text-foreground/50 text-[12px] ml-1 transition-colors"
            >
              ×
            </button>
          </div>
        )}
      </header>

      {/* Content */}
      <main className="flex-1 min-h-0">
        {effectiveTab === "graph" ? (
          <GraphTab project={selectedProject} runtime={runtime} />
        ) : (
          <StatsTab
            onSelectProject={(p) => navigate("graph", p)}
            capabilities={runtime.capabilities}
          />
        )}
      </main>
    </div>
  );
}
