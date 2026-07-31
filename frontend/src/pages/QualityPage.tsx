import { useState, useEffect, useRef } from "react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../utils/apiError";
import { qualityApi, type DuplicateGroup, type DuplicateImage } from "../api/quality";
import ConfirmDialog from "../components/common/ConfirmDialog";
import { formatFramePosition } from "../utils/duration";
import { settingsApi, type Thresholds } from "../api/settings";
import { datasetsApi } from "../api/datasets";
import { imagesApi } from "../api/images";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import StyleReferencePicker from "../components/quality/StyleReferencePicker";
import { DINO_LAYER_LABELS } from "../constants/dinoLabels";
import { QUALITY_WORKFLOW_KEY, QUALITY_FILTERS_PREFIX } from "../constants/storage";
import { loadPersisted, clearPersisted, datasetScopedKey } from "../utils/persistentState";
import { useDebouncedPersist } from "../hooks/useDebouncedPersist";

interface QualityWorkflow {
  runAesthetic: boolean;
  runTechnical: boolean;
  runWatermark: boolean;
  runEmbeddings: boolean;
  runDino: boolean;
  runNsfw: boolean;
  runDinoLayers: boolean;
  embeddingType: "clip" | "dino" | "combined";
  dinoLayer: number | "all" | null;
}

/** Rank a duplicate group best-first for *Keep best*, nulls **last**.
 *
 *  The naive `(b.aesthetic_score ?? 0) - (a.aesthetic_score ?? 0)` sorts an
 *  unscored image below a 0.1 and so deletes it preferentially — exactly
 *  backwards, since an unscored image is unknown, not bad. Scored images rank by
 *  score descending; unscored ones keep their incoming (created_at) order behind
 *  all of them. The caller disables the button entirely when nothing is scored.
 */
function rankForKeepBest(group: DuplicateGroup): DuplicateImage[] {
  return [...group].sort((a, b) => {
    const as = a.aesthetic_score, bs = b.aesthetic_score;
    if (as == null && bs == null) return 0;
    if (as == null) return 1;
    if (bs == null) return -1;
    return bs - as;
  });
}

/** The one video every member of a group came from, or null.
 *
 *  Non-null only when *every* member carries the same non-null
 *  `source_video_id` — a mixed group gets per-thumbnail labels instead, because
 *  a banner saying "these all came from clip.mp4" would be false there.
 */
function sharedSourceVideo(group: DuplicateGroup): { id: string; name: string | null } | null {
  const first = group[0]?.source_video_id;
  if (!first) return null;
  if (!group.every((m) => m.source_video_id === first)) return null;
  return { id: first, name: group[0].source_video_name };
}

const QUALITY_WORKFLOW_DEFAULTS: QualityWorkflow = {
  runAesthetic: true,
  runTechnical: true,
  runWatermark: false,
  runEmbeddings: false,
  runDino: false,
  runNsfw: false,
  runDinoLayers: false,
  embeddingType: "clip",
  dinoLayer: "all",
};

interface QualityFilters {
  activeSubfolder: string | null;
  showStyleSection: boolean;
  selectedRefIds: string[];
}

const QUALITY_FILTERS_DEFAULTS: QualityFilters = {
  activeSubfolder: null,
  showStyleSection: false,
  selectedRefIds: [],
};

const SCORING_OPTIONS = [
  { key: "aesthetic", label: "Aesthetic score · LAION", desc: "CLIP-based aesthetic predictor (1–10). Trained on human ratings.", vram: "GPU · 2.1 GB" },
  { key: "technical", label: "Technical · OpenCV", desc: "Blur, noise, near-uniform, color, saturation, brightness, pHash duplicates.", vram: "CPU only" },
  { key: "watermark", label: "Watermark detection", desc: "CLIP zero-shot classification for text overlays and logos.", vram: "GPU · 2.1 GB" },
  { key: "embeddings", label: "Style embeddings · CLIP", desc: "Required for the style-similarity workflow below.", vram: "GPU · 2.1 GB" },
  { key: "dino", label: "DINOv2 embeddings", desc: "Object-aware embedding. Can be used alone or alongside CLIP for style similarity.", vram: "GPU · 1.2 GB" },
  { key: "dino_layers", label: "DINOv2 per-layer embeds", desc: "Stores all 12 transformer layer CLS tokens. Enables per-layer style similarity.", vram: "GPU · 1.2 GB" },
  { key: "nsfw", label: "NSFW detection · Marqo", desc: "ViT classifier (Marqo/nsfw-image-detection-384) — sets the is_nsfw quality flag.", vram: "GPU · 1.0 GB" },
];

export default function QualityPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  // Remembered "workflow" config — global, shared across all datasets.
  const [workflow] = useState(() => loadPersisted(QUALITY_WORKFLOW_KEY, QUALITY_WORKFLOW_DEFAULTS));
  const [runAesthetic, setRunAesthetic] = useState(workflow.runAesthetic);
  const [runTechnical, setRunTechnical] = useState(workflow.runTechnical);
  const [runWatermark, setRunWatermark] = useState(workflow.runWatermark);
  const [runEmbeddings, setRunEmbeddings] = useState(workflow.runEmbeddings);
  const [runDino, setRunDino] = useState(workflow.runDino);
  const [runNsfw, setRunNsfw] = useState(workflow.runNsfw);
  const [jobLabel, setJobLabel] = useState("");
  const [runDinoLayers, setRunDinoLayers] = useState(workflow.runDinoLayers);
  const [embeddingType, setEmbeddingType] = useState<"clip" | "dino" | "combined">(workflow.embeddingType);
  const [dinoLayer, setDinoLayer] = useState<number | "all" | null>(workflow.dinoLayer);

  // Remembered "filters" config — per-dataset.
  const [filters] = useState(() =>
    datasetId ? loadPersisted(datasetScopedKey(QUALITY_FILTERS_PREFIX, datasetId), QUALITY_FILTERS_DEFAULTS) : QUALITY_FILTERS_DEFAULTS
  );
  const [showStyleSection, setShowStyleSection] = useState(filters.showStyleSection);
  const [selectedRefIds, setSelectedRefIds] = useState<Set<string>>(new Set(filters.selectedRefIds));
  const [externalRefFiles, setExternalRefFiles] = useState<File[]>([]);
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>(filters.activeSubfolder ?? undefined);
  // Pending *Keep best* on a same-source group, held for the confirm dialog.
  // Same-source only: refusing outright would break the legitimate case (two
  // genuinely redundant frames from one shot) and push users to work around it
  // in the gallery, so the fix is a beat of friction, not a block.
  const [pendingKeepBest, setPendingKeepBest] = useState<
    { keep: string[]; del: string[]; videoName: string | null; timestamps: (number | null)[] } | null
  >(null);

  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  useJobSSE(activeJobId);
  const jobProgress = useJobStore((s) => s.activeJobs.get(activeJobId ?? ""));

  const embeddingTypeInitialized = useRef(false);
  useEffect(() => {
    if (!embeddingTypeInitialized.current) { embeddingTypeInitialized.current = true; return; }
    if (embeddingType === "clip") setDinoLayer("all");
  }, [embeddingType]);

  // Persist the "workflow" config (scoring toggles/style settings) — global, debounced.
  useDebouncedPersist(QUALITY_WORKFLOW_KEY, {
    runAesthetic, runTechnical, runWatermark, runEmbeddings, runDino, runNsfw,
    runDinoLayers, embeddingType, dinoLayer,
  });

  // Persist "filters" config (subfolder/style refs) — per-dataset, debounced.
  useDebouncedPersist(
    datasetId ? datasetScopedKey(QUALITY_FILTERS_PREFIX, datasetId) : null,
    {
      activeSubfolder: activeSubfolder ?? null,
      showStyleSection,
      selectedRefIds: [...selectedRefIds],
    },
  );

  // Reload the "filters" blob when datasetId changes without a remount (pane mode).
  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    const next = datasetId
      ? loadPersisted(datasetScopedKey(QUALITY_FILTERS_PREFIX, datasetId), QUALITY_FILTERS_DEFAULTS)
      : QUALITY_FILTERS_DEFAULTS;
    setActiveSubfolder(next.activeSubfolder ?? undefined);
    setShowStyleSection(next.showStyleSection);
    setSelectedRefIds(new Set(next.selectedRefIds));
  }, [datasetId]);

  useEffect(() => {
    if (jobProgress?.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["duplicates", datasetId] });
      setActiveJobId(null);
    }
  }, [jobProgress?.status, datasetId, qc]);

  /* Find last completed quality score job */
  const { data: jobs } = useQuery({
    queryKey: ["jobs"],
    queryFn: async () => {
      const r = await fetch("/api/v1/jobs/?limit=50");
      return r.json() as Promise<Array<{ job_type: string; status: string; finished_at: string | null }>>;
    },
    staleTime: 60_000,
  });
  const lastScoringJob = jobs?.find((j) => j.job_type === "quality_score" && j.status === "completed");
  const lastRunLabel = lastScoringJob?.finished_at
    ? (() => {
        const diff = Date.now() - new Date(lastScoringJob.finished_at).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 60) return `${mins}m ago`;
        return `${Math.floor(mins / 60)}h ago`;
      })()
    : null;

  const { data: duplicates } = useQuery({
    queryKey: ["duplicates", datasetId],
    queryFn: () => qualityApi.duplicates(datasetId!),
    enabled: !!datasetId,
  });

  // The duplicate scan's radius is a setting, so the group header has to read it
  // rather than restate a number: it was hardcoded to "< 6" while the default is
  // 8 and the value is editable in Settings → Thresholds. Same query key and
  // staleTime as StatsPage, which renders the same threshold as a flag hint.
  const { data: thresholds } = useQuery<Thresholds>({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });

  const scoreMutation = useMutation({
    mutationFn: () =>
      qualityApi.score({
        dataset_id: datasetId!,
        subfolder: activeSubfolder,
        run_aesthetic: runAesthetic,
        run_technical: runTechnical,
        run_watermark: runWatermark,
        run_embeddings: runEmbeddings,
        run_dino: runDino,
        run_dino_layers: runDino && runDinoLayers,
        run_nsfw: runNsfw,
        label: jobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      if (data.job_id) { setActiveJobId(data.job_id); toast.success("Quality scoring started"); }
    },
    onError: () => toast.error("Failed to start scoring"),
  });

  const resolveMutation = useMutation({
    mutationFn: ({ keep, del }: { keep: string[]; del: string[] }) => qualityApi.resolveDuplicates(keep, del),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["duplicates", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      toast.success("Duplicates resolved");
    },
  });

  const similarityMutation = useMutation({
    mutationFn: async () => {
      let reference_embeddings: string[] = [];
      if (externalRefFiles.length > 0) {
        const result = await qualityApi.embedReferences(externalRefFiles);
        reference_embeddings = result.embeddings;
      }
      const effectiveType = externalRefFiles.length > 0 ? "clip" : embeddingType;
      let apiType: "clip" | "dino" | "combined" | "dino_all_layers" | "combined_all_layers" = effectiveType as typeof apiType;
      if (effectiveType === "dino" && dinoLayer === "all") apiType = "dino_all_layers";
      else if (effectiveType === "combined" && dinoLayer === "all") apiType = "combined_all_layers";
      const effectiveDinoLayer = (["dino", "combined"].includes(effectiveType) && typeof dinoLayer === "number") ? dinoLayer : undefined;
      return qualityApi.styleSimilarity({
        dataset_id: datasetId!,
        reference_image_ids: Array.from(selectedRefIds),
        reference_embeddings,
        embedding_type: apiType,
        dino_layer: effectiveDinoLayer,
      });
    },
    onSuccess: (data) => {
      const skipped = data.skipped ?? 0;
      const msg = skipped > 0
        ? `Style similarity scored for ${data.updated} images (${skipped} skipped — run embeddings first)`
        : `Style similarity scored for ${data.updated} images`;
      toast.success(msg);
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["image"] });
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, err instanceof Error ? err.message : "Style similarity scoring failed"));
    },
  });

  const toggleRef = (id: string) => {
    setSelectedRefIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  function handleResetToDefaults() {
    clearPersisted(QUALITY_WORKFLOW_KEY);
    if (datasetId) clearPersisted(datasetScopedKey(QUALITY_FILTERS_PREFIX, datasetId));

    setRunAesthetic(QUALITY_WORKFLOW_DEFAULTS.runAesthetic);
    setRunTechnical(QUALITY_WORKFLOW_DEFAULTS.runTechnical);
    setRunWatermark(QUALITY_WORKFLOW_DEFAULTS.runWatermark);
    setRunEmbeddings(QUALITY_WORKFLOW_DEFAULTS.runEmbeddings);
    setRunDino(QUALITY_WORKFLOW_DEFAULTS.runDino);
    setRunNsfw(QUALITY_WORKFLOW_DEFAULTS.runNsfw);
    setRunDinoLayers(QUALITY_WORKFLOW_DEFAULTS.runDinoLayers);
    setEmbeddingType(QUALITY_WORKFLOW_DEFAULTS.embeddingType);
    setDinoLayer(QUALITY_WORKFLOW_DEFAULTS.dinoLayer);

    setActiveSubfolder(QUALITY_FILTERS_DEFAULTS.activeSubfolder ?? undefined);
    setShowStyleSection(QUALITY_FILTERS_DEFAULTS.showStyleSection);
    setSelectedRefIds(new Set(QUALITY_FILTERS_DEFAULTS.selectedRefIds));
    setExternalRefFiles([]);

    toast.success("Configuration reset to defaults");
  }

  const dupGroups = duplicates?.groups ?? [];
  const isRunning = scoreMutation.isPending || jobProgress?.status === "running";
  const checkMap: Record<string, [boolean, (v: boolean) => void]> = {
    aesthetic: [runAesthetic, setRunAesthetic],
    technical: [runTechnical, setRunTechnical],
    watermark: [runWatermark, setRunWatermark],
    embeddings: [runEmbeddings, setRunEmbeddings],
    dino: [runDino, setRunDino],
    dino_layers: [runDinoLayers, setRunDinoLayers],
    nsfw: [runNsfw, setRunNsfw],
  };

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h">
        <div>
          <h1>Score images</h1>
          <p>Run aesthetic, technical, watermark and embedding analysis on the dataset.</p>
        </div>
      </div>

      {/* Run scoring panel */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h">
          {lastRunLabel && (
            <span className="badge dot info">Last run · {lastRunLabel}</span>
          )}
          <h3 style={{ marginLeft: lastRunLabel ? 12 : 0 }}>Run quality analysis</h3>
          <div style={{ flex: 1 }} />
          <button className="btn ghost sm" onClick={handleResetToDefaults} title="Clear remembered configuration and revert to defaults">
            Reset to defaults
          </button>
          {subfolders.some((sf) => sf.path) && (
            <select
              className="select"
              value={activeSubfolder ?? ""}
              onChange={(e) => setActiveSubfolder(e.target.value === "" ? undefined : e.target.value)}
              style={{ fontSize: 12, height: 30, marginRight: 8 }}
              disabled={isRunning}
            >
              <option value="">All subfolders</option>
              {subfolders.filter((sf) => sf.path).map((sf) => (
                <option key={sf.path} value={sf.path}>{sf.path} ({sf.image_count})</option>
              ))}
            </select>
          )}
          <input
            className="input"
            type="text"
            placeholder="Job label (optional)"
            value={jobLabel}
            onChange={(e) => setJobLabel(e.target.value)}
            style={{ width: 180, fontSize: 12 }}
            title="Optional name shown in the job queue"
          />
          <button className="btn primary" onClick={() => scoreMutation.mutate()} disabled={isRunning}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M2.5 8a5.5 5.5 0 1010-2"/><path d="M11 3.5l1.5 2.5L10 7"/>
            </svg>
            {isRunning ? "Scoring…" : "Run scoring"}
          </button>
        </div>
        <div className="panel-b">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {SCORING_OPTIONS.filter((o) => {
              if (o.key === "dino_layers") return runDino;
              return true;
            }).map((opt) => {
              const [checked, setChecked] = checkMap[opt.key];
              return (
                <label key={opt.key} className={`model-row${checked ? " sel" : ""}`} style={{ cursor: "pointer" }}>
                  <input type="checkbox" className="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} style={{ marginRight: 4 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="mr-name">{opt.label}</div>
                    <div className="mr-desc">{opt.desc}</div>
                  </div>
                  <span className="mr-vram">{opt.vram}</span>
                </label>
              );
            })}
          </div>

          {/* Progress */}
          {jobProgress && (
            <div style={{ marginTop: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--fg-mute)", marginBottom: 6 }}>
                <span>{jobProgress.message}</span>
                <span className="mono">{jobProgress.done}/{jobProgress.total}</span>
              </div>
              <div style={{ height: 5, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${jobProgress.percent ?? 0}%`, background: "linear-gradient(90deg, var(--accent-2), var(--accent))", transition: "width .4s" }} />
              </div>
              {jobProgress.status === "completed" && <p style={{ color: "var(--good)", fontSize: 12, marginTop: 6 }}>✓ Scoring complete</p>}
            </div>
          )}
        </div>
      </div>

      {/* Style similarity panel */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h">
          <h3>Style similarity</h3>
          <div style={{ flex: 1 }} />
          <span className="mono" style={{ color: "var(--fg-dim)", fontSize: 11 }}>Cosine similarity to reference embeddings</span>
          <button className="icon-btn" onClick={() => setShowStyleSection((v) => !v)}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d={showStyleSection ? "M3 10l5-5 5 5" : "M3 6l5 5 5-5"}/>
            </svg>
          </button>
        </div>

        {showStyleSection && (
          <div style={{ padding: "4px 22px" }}>
            <div className="form-row">
              <div className="lbl-col">
                <h4>Embedding model</h4>
                <p>CLIP for general images; DINOv2 for object-shape similarity; CLIP + DINOv2 blends both (0.38 × CLIP + 0.62 × DINOv2). All require embeddings computed first.</p>
              </div>
              <div className="row-flex">
                <button className={`btn sm${embeddingType === "clip" ? " primary" : ""}`} onClick={() => setEmbeddingType("clip")}>CLIP</button>
                <button className={`btn sm${embeddingType === "dino" ? " primary" : ""}`} onClick={() => setEmbeddingType("dino")} disabled={externalRefFiles.length > 0}>DINOv2</button>
                <button className={`btn sm${embeddingType === "combined" ? " primary" : ""}`} onClick={() => setEmbeddingType("combined")} disabled={externalRefFiles.length > 0}>CLIP + DINOv2</button>
              </div>
            </div>

            {(embeddingType === "dino" || embeddingType === "combined") && externalRefFiles.length === 0 && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>DINOv2 layer</h4>
                  <p>Each transformer block captures increasingly abstract features. "Final" uses the pre-computed <span className="mono">dino_embedding</span>. All others require per-layer embeddings. "All layers" scores each layer independently and stores results for comparison in the image detail view.</p>
                </div>
                <select
                  className="select"
                  value={dinoLayer === "all" ? "all" : (dinoLayer ?? 12)}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "all") setDinoLayer("all");
                    else { const n = Number(v); setDinoLayer(n === 12 ? null : n); }
                  }}
                >
                  {Array.from({ length: 12 }, (_, i) => i + 1).map((n) => (
                    <option key={n} value={n}>Layer {n} — {DINO_LAYER_LABELS[String(n)]}</option>
                  ))}
                  <option value="all">{embeddingType === "combined" ? "All layers — Score CLIP + each DINOv2 layer individually" : "All layers — Score each layer individually"}</option>
                </select>
              </div>
            )}

            <div className="form-row">
              <div className="lbl-col">
                <h4>Reference images</h4>
                <p>Pick from the dataset, or drag in local files (always embedded with CLIP).</p>
              </div>
              {datasetId && (
                <StyleReferencePicker
                  datasetId={datasetId}
                  selectedIds={selectedRefIds}
                  onToggle={toggleRef}
                  externalFiles={externalRefFiles}
                  onExternalFilesChange={setExternalRefFiles}
                />
              )}
            </div>

            <div className="form-row">
              <div className="lbl-col">
                <h4>Action</h4>
                <p>Score writes <span className="mono">style_similarity_score</span> per image. CPU-only, runs immediately.</p>
              </div>
              <button
                className="btn primary"
                onClick={() => similarityMutation.mutate()}
                disabled={(selectedRefIds.size === 0 && externalRefFiles.length === 0) || similarityMutation.isPending}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <path d="M2.5 8a5.5 5.5 0 1010-2"/><path d="M11 3.5l1.5 2.5L10 7"/>
                </svg>
                Score similarity
                {(selectedRefIds.size + externalRefFiles.length) > 0 && ` · ${selectedRefIds.size + externalRefFiles.length} refs`}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Duplicates */}
      {dupGroups.length > 0 && (
        <div className="panel">
          <div className="panel-h">
            <h3>Duplicate groups</h3>
            <span className="badge warn dot">{dupGroups.length} groups</span>
          </div>
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {dupGroups.map((group, gi) => {
              const shared = sharedSourceVideo(group);
              const anyScored = group.some((m) => m.aesthetic_score != null);
              const anyLineage = group.some((m) => m.source_video_id != null);
              return (
              <div key={gi} style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 14, alignItems: "center", padding: 12, background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
                <div>
                  <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 8 }}>
                    {group.length} similar images
                    {thresholds && ` · perceptual hash distance < ${thresholds.duplicate_threshold}`}
                  </div>
                  {/* Same-source banner. A perceptual hash cannot tell a held
                      animation cel or recycled footage from a redundant copy, so
                      say where these came from before anything is deleted. */}
                  {shared && (
                    <div
                      style={{
                        marginBottom: 8, padding: "6px 9px", borderRadius: "var(--r-sm)",
                        background: "rgba(210,154,58,.10)", border: "1px solid rgba(210,154,58,.35)",
                        fontSize: 11.5, color: "var(--warn)",
                      }}
                    >
                      All {group.length} frames came from{" "}
                      <span className="mono">{shared.name ?? "a video that has since been deleted"}</span>.
                      Held animation cels and recycled footage look identical to a perceptual hash —
                      check the timestamps before deleting.
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                    {group.map((img) => (
                      <div key={img.id} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                        <img
                          src={imagesApi.thumbnailUrlVersioned(img.id, img.updated_at)}
                          alt={img.filename}
                          title={img.kept ? "Kept by the scan — the other copies point at this one" : undefined}
                          style={{ width: 64, height: 64, objectFit: "cover", borderRadius: "var(--r-sm)", border: img.kept ? "2px solid var(--good)" : "1px solid var(--line-2)" }}
                        />
                        <span className="mono" style={{ fontSize: 10, color: img.kept ? "var(--good)" : "var(--fg-dim)", textAlign: "center", maxWidth: 64, overflow: "hidden", textOverflow: "ellipsis" }}>{img.filename}</span>
                        {img.kept && <span style={{ fontSize: 10, color: "var(--good)" }}>kept</span>}
                        {/* A mixed group names the video per thumbnail — the
                            banner above would be false there. */}
                        {!shared && anyLineage && img.source_video_id && (
                          <span className="mono" style={{ fontSize: 9.5, color: "var(--warn)", textAlign: "center", maxWidth: 64, overflow: "hidden", textOverflow: "ellipsis" }} title={img.source_video_name ?? undefined}>
                            {img.source_video_name ?? "video"}
                          </span>
                        )}
                        {img.source_timestamp_ms != null && (
                          <span className="mono" style={{ fontSize: 9.5, color: "var(--fg-dim)" }}>
                            {formatFramePosition(img.source_timestamp_ms)}
                            {img.source_shot_index != null && ` · shot ${img.source_shot_index}`}
                          </span>
                        )}
                        {img.aesthetic_score != null && <span className="mono" style={{ fontSize: 11, color: "var(--good)" }}>{img.aesthetic_score.toFixed(1)}</span>}
                      </div>
                    ))}
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <button
                    className="btn sm primary"
                    // "Best" is meaningless with nothing scored: the old code
                    // silently kept whichever member came first and called it
                    // best. Say so instead of pretending.
                    disabled={!anyScored}
                    title={anyScored ? undefined : "No image in this group has an aesthetic score — run scoring first, or use Keep first."}
                    onClick={() => {
                      const best = rankForKeepBest(group);
                      const payload = { keep: [best[0].id], del: best.slice(1).map((i) => i.id) };
                      if (shared) {
                        setPendingKeepBest({
                          ...payload,
                          videoName: shared.name,
                          timestamps: best.slice(1).map((i) => i.source_timestamp_ms),
                        });
                      } else {
                        resolveMutation.mutate(payload);
                      }
                    }}
                  >Keep best</button>
                  {/* group[0] is the image the scan kept — see get_duplicates.
                      No confirm: it keeps the scan's own choice rather than a
                      score-driven one, so it stays one click. */}
                  <button className="btn sm" onClick={() => resolveMutation.mutate({ keep: [group[0].id], del: group.slice(1).map((i) => i.id) })}>Keep first</button>
                </div>
              </div>
              );
            })}
          </div>
        </div>
      )}

      {pendingKeepBest && (
        <ConfirmDialog
          title="Delete frames from one video?"
          message={
            `Every image in this group was extracted from ${pendingKeepBest.videoName ?? "the same video"}. ` +
            `This deletes ${pendingKeepBest.del.length} frame${pendingKeepBest.del.length === 1 ? "" : "s"} at ` +
            `${pendingKeepBest.timestamps.map((t) => formatFramePosition(t)).join(", ")} and keeps the highest-scoring one. ` +
            `Held animation cels and recycled footage hash the same as a redundant copy, so these may be distinct shots.`
          }
          confirmLabel="Delete frames"
          danger
          onCancel={() => setPendingKeepBest(null)}
          onConfirm={() => {
            resolveMutation.mutate({ keep: pendingKeepBest.keep, del: pendingKeepBest.del });
            setPendingKeepBest(null);
          }}
        />
      )}
    </div>
  );
}
