import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronRight } from "lucide-react";
import toast from "react-hot-toast";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import { datasetsApi } from "../api/datasets";
import { jobsApi } from "../api/jobs";
import { tagConsolidationApi, type AnalyzeResult, type TagCluster } from "../api/tagConsolidation";
import { TAG_CONSOLIDATE_WORKFLOW_KEY } from "../constants/storage";
import { loadPersisted, savePersisted } from "../utils/persistentState";

interface EditCluster {
  id: number;
  canonical: string;
  variants: { tag: string; count: number }[];
  excluded: Set<string>;
  accepted: boolean;
  minSim: number;
}

type SortMode = "impact" | "size" | "review" | "alpha";

function toEditClusters(clusters: TagCluster[]): EditCluster[] {
  return clusters.map((c, i) => ({
    id: i,
    canonical: c.canonical,
    variants: c.variants,
    excluded: new Set<string>(),
    accepted: true,
    minSim: c.min_sim ?? 0,
  }));
}

/** Tag occurrences that would be rewritten for this cluster as currently configured. */
function clusterImpact(c: EditCluster): number {
  return c.variants.reduce(
    (sum, v) => (v.tag !== c.canonical && !c.excluded.has(v.tag) ? sum + v.count : sum),
    0,
  );
}

const CACHE_KEYS = ["images", "dataset-stats", "tag-stats", "score-values", "tag-cooccurrence"];

export default function TagConsolidatePage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();

  const [threshold, setThreshold] = useState(
    () => loadPersisted(TAG_CONSOLIDATE_WORKFLOW_KEY, { threshold: 0.85 }).threshold,
  );
  const [subfolder, setSubfolder] = useState<string | undefined>(undefined);
  // Deliberately a synchronous write, not useDebouncedPersist: the payload is a
  // single number, so there is no debounce and therefore no window in which an
  // unmount could drop a write. Adopting the hook here would *add* a 350ms delay
  // rather than fix anything. See docs/dev/frontend-core.md.
  useEffect(() => {
    savePersisted(TAG_CONSOLIDATE_WORKFLOW_KEY, { threshold });
  }, [threshold]);

  const [analyzeJobId, setAnalyzeJobId] = useState<string | null>(null);
  const [applyJobId, setApplyJobId] = useState<string | null>(null);
  const [clusters, setClusters] = useState<EditCluster[] | null>(null);
  const [summary, setSummary] = useState<Pick<AnalyzeResult, "vocab_size" | "image_count" | "truncated"> | null>(null);
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("impact");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [subsuming, setSubsuming] = useState(false);

  useJobSSE(analyzeJobId);
  useJobSSE(applyJobId);
  const analyzeJob = useJobStore((s) => s.activeJobs.get(analyzeJobId ?? ""));
  const applyJob = useJobStore((s) => s.activeJobs.get(applyJobId ?? ""));

  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  // Quick-cleanup dry-run preview (how many captions subsumption would change).
  const subsumePreview = useQuery({
    queryKey: ["subsume-preview", datasetId, subfolder ?? null],
    queryFn: () => tagConsolidationApi.subsume(datasetId!, { subfolder, dry_run: true }),
    enabled: !!datasetId,
  });

  const invalidateCaptionCaches = useCallback(() => {
    for (const key of CACHE_KEYS) qc.invalidateQueries({ queryKey: [key, datasetId] });
    // Per-image caption/detail query families used by ImageDetailPage so an open
    // detail view refreshes immediately rather than waiting out staleTime.
    qc.invalidateQueries({ queryKey: ["caption"] });
    qc.invalidateQueries({ queryKey: ["image"] });
    qc.invalidateQueries({ queryKey: ["subsume-preview", datasetId] });
  }, [qc, datasetId]);

  // Analyze job completion → load proposal once.
  const consumedAnalyze = useRef<string | null>(null);
  useEffect(() => {
    if (!analyzeJobId || !analyzeJob) return;
    if (analyzeJob.status === "completed" && consumedAnalyze.current !== analyzeJobId) {
      consumedAnalyze.current = analyzeJobId;
      jobsApi.get(analyzeJobId).then((job) => {
        const r = job.result_data as unknown as AnalyzeResult;
        setClusters(toEditClusters(r.clusters ?? []));
        setSummary({ vocab_size: r.vocab_size, image_count: r.image_count, truncated: r.truncated });
        setExpanded(new Set());
      });
    } else if (analyzeJob.status === "failed" && consumedAnalyze.current !== analyzeJobId) {
      consumedAnalyze.current = analyzeJobId;
      toast.error("Analysis failed");
    }
  }, [analyzeJobId, analyzeJob]);

  // Apply job completion → refresh caches.
  const consumedApply = useRef<string | null>(null);
  useEffect(() => {
    if (!applyJobId || !applyJob) return;
    if (applyJob.status === "completed" && consumedApply.current !== applyJobId) {
      consumedApply.current = applyJobId;
      jobsApi.get(applyJobId).then((job) => {
        const affected = (job.result_data as { affected?: number }).affected ?? 0;
        toast.success(`Consolidated tags in ${affected} caption${affected !== 1 ? "s" : ""}`);
      });
      invalidateCaptionCaches();
      setClusters(null);
      setSummary(null);
    } else if (applyJob.status === "failed" && consumedApply.current !== applyJobId) {
      consumedApply.current = applyJobId;
      toast.error("Apply failed");
    }
  }, [applyJobId, applyJob, invalidateCaptionCaches]);

  const analyzing = !!analyzeJob && (analyzeJob.status === "running" || analyzeJob.status === "pending");
  const applying = !!applyJob && (applyJob.status === "running" || applyJob.status === "pending");

  const mapping = useMemo(() => {
    const m: Record<string, string> = {};
    for (const c of clusters ?? []) {
      if (!c.accepted) continue;
      for (const v of c.variants) {
        if (v.tag === c.canonical || c.excluded.has(v.tag)) continue;
        m[v.tag] = c.canonical;
      }
    }
    return m;
  }, [clusters]);
  const mappingCount = Object.keys(mapping).length;

  const displayed = useMemo(() => {
    let list = clusters ?? [];
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (c) => c.canonical.toLowerCase().includes(q) || c.variants.some((v) => v.tag.toLowerCase().includes(q)),
      );
    }
    const sorted = [...list];
    sorted.sort((a, b) => {
      switch (sortMode) {
        case "size": return b.variants.length - a.variants.length;
        case "review": return a.minSim - b.minSim;
        case "alpha": return a.canonical.localeCompare(b.canonical);
        default: return clusterImpact(b) - clusterImpact(a);
      }
    });
    return sorted;
  }, [clusters, search, sortMode]);

  function patchCluster(id: number, patch: Partial<EditCluster>) {
    setClusters((prev) => prev && prev.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  }
  function toggleExcluded(id: number, tag: string) {
    setClusters((prev) =>
      prev &&
      prev.map((c) => {
        if (c.id !== id) return c;
        const excluded = new Set(c.excluded);
        if (excluded.has(tag)) excluded.delete(tag);
        else excluded.add(tag);
        return { ...c, excluded };
      }),
    );
  }
  function setAllAccepted(accepted: boolean) {
    setClusters((prev) => prev && prev.map((c) => ({ ...c, accepted })));
  }
  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function runAnalyze() {
    if (!datasetId) return;
    setClusters(null);
    setSummary(null);
    const res = await tagConsolidationApi.analyze(datasetId, { threshold, subfolder });
    if (!res.job_id) {
      toast(res.message ?? "Nothing to analyze");
      return;
    }
    setAnalyzeJobId(res.job_id);
  }

  async function runApply() {
    if (!datasetId || mappingCount === 0) return;
    const res = await tagConsolidationApi.apply(datasetId, { mapping, subfolder });
    if (!res.job_id) {
      toast(res.message ?? "Nothing to apply");
      return;
    }
    setApplyJobId(res.job_id);
  }

  async function runSubsume() {
    if (!datasetId) return;
    setSubsuming(true);
    try {
      const res = await tagConsolidationApi.subsume(datasetId, { subfolder, dry_run: false });
      if (res.affected === 0) toast("No redundant tags found");
      else toast.success(`Cleaned ${res.affected} caption${res.affected !== 1 ? "s" : ""}`);
      invalidateCaptionCaches();
    } finally {
      setSubsuming(false);
    }
  }

  // Virtualized cluster list
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: displayed.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 46,
    overscan: 8,
  });

  if (!datasetId) {
    return <div className="p-6 text-gray-400">Select a dataset to consolidate tags.</div>;
  }

  const previewCount = subsumePreview.data?.affected ?? 0;
  const previewTotal = (subsumePreview.data?.affected ?? 0) + (subsumePreview.data?.skipped ?? 0);

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <div>
        <h2 className="text-lg font-medium">Consolidate Tags</h2>
        <p className="text-sm text-gray-400">
          Reduce redundant and synonymous wording across captions. Works on comma-separated
          segments — individual tags for booru-style captions, or whole phrases/sentences for
          natural-language captions.
        </p>
      </div>

      {subfolders.some((s) => s.path) && (
        <div className="flex items-center gap-2">
          <label className="label mb-0">Scope</label>
          <select className="input" value={subfolder ?? ""} onChange={(e) => setSubfolder(e.target.value || undefined)}>
            <option value="">All subfolders</option>
            {subfolders.filter((s) => s.path).map((s) => (
              <option key={s.path} value={s.path}>{s.path} ({s.image_count})</option>
            ))}
          </select>
        </div>
      )}

      {/* Quick cleanup (subsumption) */}
      <div className="card p-4 space-y-2">
        <h3 className="font-medium">Quick cleanup</h3>
        <p className="text-sm text-gray-400">
          Remove redundant tags or repeated phrases within each caption — drops <span className="mono">tail</span> when{" "}
          <span className="mono">long tail</span> is present, and collapses exact duplicates. Deterministic, no model.
        </p>
        <div className="flex items-center gap-3">
          <button className="btn-primary" onClick={runSubsume} disabled={subsuming || previewCount === 0}>
            {subsuming ? "Cleaning…" : "Run cleanup"}
          </button>
          <span className="text-xs text-gray-400">
            {subsumePreview.isLoading
              ? "Checking…"
              : `${previewCount.toLocaleString()} of ${previewTotal.toLocaleString()} captions affected`}
          </span>
        </div>
      </div>

      {/* Find synonyms (semantic) */}
      <div className="card p-4 space-y-3">
        <h3 className="font-medium">Find synonyms</h3>
        <p className="text-sm text-gray-400">
          Cluster semantically similar tags or phrases (e.g. <span className="mono">car</span> /{" "}
          <span className="mono">automobile</span>) and merge each to one canonical form. The embedding
          model handles natural-language captions too, not just booru tags.
        </p>
        <div className="form-row">
          <label className="label">Similarity threshold: {threshold.toFixed(2)}</label>
          <input
            type="range" min={0.5} max={1} step={0.01} value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value))} className="w-full"
          />
          <p className="text-xs text-gray-500">Higher = stricter (fewer, tighter clusters). 0.85 is a good start.</p>
        </div>
        <div className="flex items-center gap-3">
          <button className="btn-primary" onClick={runAnalyze} disabled={analyzing}>
            {analyzing ? `Analyzing… ${analyzeJob?.percent?.toFixed(0) ?? 0}%` : "Analyze"}
          </button>
          {analyzing && <span className="text-xs text-gray-400">{analyzeJob?.message}</span>}
        </div>
      </div>

      {/* Results */}
      {summary && clusters && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap sticky top-0 bg-[var(--bg)] py-2 z-10">
            <span className="text-sm text-gray-400">
              {clusters.length} cluster{clusters.length !== 1 ? "s" : ""} · {summary.vocab_size} tags
              {summary.truncated && " (truncated)"}
            </span>
            <input
              className="input flex-1 min-w-[140px]" placeholder="Filter tags…"
              value={search} onChange={(e) => setSearch(e.target.value)}
            />
            <select className="input" value={sortMode} onChange={(e) => setSortMode(e.target.value as SortMode)}>
              <option value="impact">Sort: Impact</option>
              <option value="size">Sort: Cluster size</option>
              <option value="review">Sort: Needs review</option>
              <option value="alpha">Sort: A–Z</option>
            </select>
            <button className="btn btn-sm btn-secondary" onClick={() => setAllAccepted(true)}>Accept all</button>
            <button className="btn btn-sm btn-secondary" onClick={() => setAllAccepted(false)}>Skip all</button>
            <button className="btn-primary" onClick={runApply} disabled={applying || mappingCount === 0}>
              {applying ? `Applying… ${applyJob?.percent?.toFixed(0) ?? 0}%` : `Apply (${mappingCount})`}
            </button>
          </div>

          {clusters.length === 0 && (
            <p className="text-sm text-gray-500">No similar-tag clusters found at this threshold.</p>
          )}

          {displayed.length > 0 && (
            <div ref={scrollRef} className="card" style={{ height: "60vh", overflow: "auto" }}>
              <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
                {rowVirtualizer.getVirtualItems().map((vi) => {
                  const c = displayed[vi.index];
                  const isOpen = expanded.has(c.id);
                  const impact = clusterImpact(c);
                  const others = c.variants.filter((v) => v.tag !== c.canonical);
                  return (
                    <div
                      key={c.id}
                      data-index={vi.index}
                      ref={rowVirtualizer.measureElement}
                      style={{
                        position: "absolute", top: 0, left: 0, width: "100%",
                        transform: `translateY(${vi.start}px)`,
                      }}
                      className={`border-b border-[var(--line-2)] px-3 py-2 ${c.accepted ? "" : "opacity-45"}`}
                    >
                      <div className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox" checked={c.accepted}
                          onChange={(e) => patchCluster(c.id, { accepted: e.target.checked })}
                          title="Accept this merge"
                        />
                        <button className="text-gray-400 hover:text-gray-200" onClick={() => toggleExpand(c.id)}>
                          {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        </button>
                        <span className="font-medium">{c.canonical}</span>
                        <span className="text-gray-500">←</span>
                        <span className="text-gray-400 truncate flex-1" title={others.map((v) => v.tag).join(", ")}>
                          {others.map((v) => v.tag).join(", ")}
                        </span>
                        <span className="text-xs text-gray-500 whitespace-nowrap">{impact} uses</span>
                        <span className="text-xs text-gray-500 whitespace-nowrap" title="Minimum pairwise similarity">
                          sim {c.minSim.toFixed(2)}
                        </span>
                      </div>

                      {isOpen && (
                        <div className="mt-2 ml-6 space-y-2">
                          <div className="flex items-center gap-2">
                            <label className="label mb-0">Merge to</label>
                            <input
                              className="input" list={`canon-${c.id}`} value={c.canonical}
                              onChange={(e) => patchCluster(c.id, { canonical: e.target.value })}
                            />
                            <datalist id={`canon-${c.id}`}>
                              {c.variants.map((v) => <option key={v.tag} value={v.tag} />)}
                            </datalist>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {c.variants.map((v) => {
                              const isCanonical = v.tag === c.canonical;
                              const isExcluded = c.excluded.has(v.tag);
                              return (
                                <button
                                  key={v.tag} disabled={isCanonical}
                                  onClick={() => toggleExcluded(c.id, v.tag)}
                                  title={isCanonical ? "Canonical" : isExcluded ? "Excluded — left unchanged" : "Will be merged"}
                                  className={`btn btn-sm ${isCanonical ? "btn-primary" : "btn-secondary"}`}
                                  style={
                                    isExcluded
                                      ? {
                                          background: "var(--bad-bg)",
                                          borderColor: "var(--bad)",
                                          color: "var(--bad)",
                                          textDecoration: "line-through",
                                        }
                                      : undefined
                                  }
                                >
                                  {v.tag} <span className="text-xs opacity-60">×{v.count}</span>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
