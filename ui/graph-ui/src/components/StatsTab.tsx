import { useMemo, useState, useEffect } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useProjects } from "../hooks/useProjects";
import { colorForLabel } from "../lib/colors";
import { useUiMessages } from "../lib/i18n";
import { ALL_CAPABILITIES, type CapabilitySet } from "../lib/kgAdapter";

interface StatsTabProps {
  onSelectProject: (project: string) => void;
  /* Capability gates — default to all enabled so existing callers keep the
   * full upstream panel. */
  capabilities?: CapabilitySet;
}

const ALL_GATES: CapabilitySet = { ...ALL_CAPABILITIES };

/* Unsupported health/ADR/browse routes intentionally have no UI. */


/* ── Create Index Modal ─────────────────────────────────── */

function CreateIndexModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const t = useUiMessages();
  const [path, setPath] = useState("");
  const [projectName, setProjectName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    if (!path) return;
    setSubmitting(true); setError(null);
    const fallback = path.split(/[\\/]+/).filter(Boolean).pop() ?? "project";
    const derived = fallback.replace(/[^A-Za-z0-9._-]/g, "-").slice(0, 128) || "project";
    try {
      const res = await fetch("/api/index", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ root_path: path, project_name: projectName.trim() || derived }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Failed");
      onCreated(); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setSubmitting(false); }
  };
  return <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
    <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
    <div className="relative bg-[#0e2028] border border-border/40 rounded-2xl p-6 w-full max-w-lg shadow-2xl" onClick={(e) => e.stopPropagation()}>
      <h3 className="text-[15px] font-semibold text-foreground/90 mb-1">{t.index.selectRepositoryFolder}</h3>
      <p className="text-[12px] text-foreground/30 mb-5">{t.index.instructions}</p>
      <label className="block mb-3"><span className="block text-[10px] uppercase tracking-widest text-foreground/25 mb-1">{t.index.repositoryPath}</span><input aria-label={t.index.repositoryPath} value={path} onChange={(e) => setPath(e.target.value)} className="w-full bg-white/[0.04] border border-white/[0.06] rounded-lg px-3 py-2 text-[12px] text-foreground font-mono" /></label>
      <label className="block mb-5"><span className="block text-[10px] uppercase tracking-widest text-foreground/25 mb-1">{t.index.projectName}</span><input aria-label={t.index.projectName} value={projectName} onChange={(e) => setProjectName(e.target.value)} className="w-full bg-white/[0.04] border border-white/[0.06] rounded-lg px-3 py-2 text-[12px] text-foreground" /></label>
      {error && <p className="text-destructive text-[11px] mb-3">{error}</p>}
      <div className="flex justify-end gap-2"><button onClick={onClose} className="px-3 py-2 text-[12px]">{t.common.cancel}</button><button onClick={submit} disabled={submitting || !path} className="px-4 py-2 rounded-lg bg-primary/20 text-primary text-[12px] disabled:opacity-30">{submitting ? t.index.starting : t.index.indexThisFolder}</button></div>
    </div>
  </div>;
}

/* ── Index Progress ─────────────────────────────────────── */

export function IndexProgress({ onDone }: { onDone: () => void }) {
  const t = useUiMessages();
  const [jobs, setJobs] = useState<{ slot: number; status: string; path: string; error?: string }[]>([]);
  const [hasActive, setHasActive] = useState(true);
  useEffect(() => {
    if (!hasActive) return;
    const poll = setInterval(async () => {
      try {
        const data = await (await fetch("/api/index-status")).json();
        setJobs(data);
        const stillIndexing = data.some((j: { status: string }) => j.status === "indexing");
        /* Empty list = job not visible: the backend keeps finished jobs listed
           as "done"/"error", so [] mid-index only happens on transient state
           loss (e.g. server restart) — keep polling, don't treat as done. */
        if (data.length > 0 && !stillIndexing) {
          setHasActive(false);
          const hasErrors = data.some((j: { status: string }) => j.status === "error");
          if (!hasErrors) {
            onDone();
          }
        }
      } catch (error) {
        console.error("[IndexProgress] Poll failed:", error);
      }
    }, 2000);
    return () => clearInterval(poll);
  }, [onDone, hasActive]);

  const active = jobs.filter((j) => j.status === "indexing");
  const errors = jobs.filter((j) => j.status === "error");

  if (active.length === 0 && errors.length === 0) return null;

  return (
    <div className="rounded-xl border border-primary/20 bg-primary/5 p-4 mb-6">
      {active.map((j) => (
        <div key={j.slot} className="flex items-center gap-3">
          <div className="w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin shrink-0" />
          <div>
            <p className="text-[12px] text-primary font-medium">{t.projects.indexingInProgress}</p>
            <p className="text-[11px] text-foreground/30 font-mono">{j.path}</p>
          </div>
        </div>
      ))}
      {errors.map((j) => (
        <div key={j.slot} className="flex items-start gap-3 mt-3 first:mt-0 p-3 rounded-lg border border-destructive/20 bg-destructive/5 text-destructive">
          <span className="text-[14px]">⚠️</span>
          <div className="flex-1 min-w-0">
            <p className="text-[12px] font-semibold">{t.projects.indexingFailed}</p>
            <p className="text-[11px] font-mono truncate">{j.path}</p>
            {j.error && <p className="text-[10px] opacity-75 mt-1 font-mono">{j.error}</p>}
          </div>
        </div>
      ))}
      {errors.length > 0 && (
        <div className="flex justify-end mt-3">
          <button
            onClick={onDone}
            className="px-3 py-1 rounded bg-destructive/10 hover:bg-destructive/20 text-destructive text-[11px] font-medium transition-all"
          >
            {t.common.dismiss}
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Main Stats Tab ─────────────────────────────────────── */

export function StatsTab({ onSelectProject, capabilities = ALL_GATES }: StatsTabProps) {
  const t = useUiMessages();
  const { projects, loading, error, refresh } = useProjects();
  const [showModal, setShowModal] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const canIndex = capabilities.index;
  const aggregate = useMemo(() => {
    let totalNodes = 0, totalEdges = 0;
    for (const p of projects) {
      totalNodes += p.schema?.node_labels?.reduce((s, l) => s + l.count, 0) ?? 0;
      totalEdges += p.schema?.edge_types?.reduce((s, t) => s + t.count, 0) ?? 0;
    }
    return { projects: projects.length, nodes: totalNodes, edges: totalEdges };
  }, [projects]);


  return (
    <ScrollArea className="h-full">
      <div className="p-8 max-w-3xl mx-auto">
        {projects.length > 0 && (
          <div className="flex gap-4 mb-8">
            {[
              { label: t.tabs.projects, value: aggregate.projects, color: "text-primary" },
              { label: t.projects.nodes, value: aggregate.nodes, color: "text-foreground/80" },
              { label: t.projects.edges, value: aggregate.edges, color: "text-foreground/80" },
            ].map((s) => (
              <div key={s.label} className="flex-1 rounded-xl border border-border/30 bg-white/[0.02] p-4">
                <p className="text-[10px] text-foreground/25 uppercase tracking-widest mb-1">{s.label}</p>
                <p className={`text-[22px] font-semibold tabular-nums ${s.color}`}>{s.value.toLocaleString()}</p>
              </div>
            ))}
          </div>
        )}

        {canIndex && indexing && <IndexProgress onDone={() => { setIndexing(false); refresh(); }} />}

        <div className="flex items-center justify-between mb-6">
          <h2 className="text-[15px] font-semibold text-foreground/80">{t.projects.indexedProjects}</h2>
          <div className="flex items-center gap-2">
            {canIndex && <button onClick={() => setShowModal(true)} className="px-3 py-1.5 rounded-lg bg-primary/15 hover:bg-primary/25 text-primary text-[12px] font-medium transition-all">+ {t.index.newIndex}</button>}
            <button onClick={refresh} disabled={loading} className="px-3 py-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.07] text-[12px] text-foreground/40 font-medium transition-all disabled:opacity-30">{loading ? "..." : t.common.refresh}</button>
          </div>
        </div>

        {error && <div className="rounded-xl border border-destructive/20 bg-destructive/5 p-4 mb-6"><p className="text-destructive text-[13px]">{error}</p></div>}

        {!loading && projects.length === 0 && !error && (
          <div className="text-center py-20">
            <p className="text-foreground/25 text-[13px] mb-2">{t.projects.noIndexedProjects}</p>
            {canIndex && <button onClick={() => setShowModal(true)} className="px-4 py-2 rounded-lg bg-primary/15 hover:bg-primary/25 text-primary text-[12px] font-medium transition-all">{t.projects.indexFirstRepository}</button>}
          </div>
        )}

        <div className="space-y-3">
          {projects.map((p) => {
            const totalNodes = p.schema?.node_labels?.reduce((s, l) => s + l.count, 0) ?? 0;
            const totalEdges = p.schema?.edge_types?.reduce((s, t) => s + t.count, 0) ?? 0;
            return (
              <div key={p.project.name} className="rounded-xl border border-border/30 bg-white/[0.02] hover:bg-white/[0.035] transition-all p-5">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div className="min-w-0 flex items-start gap-2.5">
                    <div className="min-w-0">
                      <h3 className="text-[14px] font-semibold text-foreground/90 mb-0.5">{p.project.name}</h3>
                      <p className="text-[11px] text-foreground/20 font-mono truncate">{p.project.root_path}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button onClick={() => onSelectProject(p.project.name)} className="px-3 py-1.5 rounded-lg bg-primary/15 hover:bg-primary/25 text-primary text-[12px] font-medium transition-all">{t.projects.viewGraph}</button>
                  </div>
                </div>
                {p.schema && (
                  <>
                    <div className="flex gap-6 text-[12px] text-foreground/30 mb-3">
                      <span><strong className="text-foreground/55 tabular-nums">{totalNodes.toLocaleString()}</strong> {t.projects.nodes}</span>
                      <span><strong className="text-foreground/55 tabular-nums">{totalEdges.toLocaleString()}</strong> {t.projects.edges}</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {p.schema.node_labels?.map((l) => (
                        <span key={l.label} className="inline-flex items-center gap-1 px-1.5 py-[2px] rounded-md text-[10px] font-medium" style={{ backgroundColor: colorForLabel(l.label) + "10", color: colorForLabel(l.label) + "bb" }}>
                          <span className="w-[4px] h-[4px] rounded-full" style={{ backgroundColor: colorForLabel(l.label) }} />
                          {l.label} {l.count.toLocaleString()}
                        </span>
                      ))}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
      {canIndex && showModal && <CreateIndexModal onClose={() => setShowModal(false)} onCreated={() => { setIndexing(true); refresh(); }} />}
    </ScrollArea>
  );
}
