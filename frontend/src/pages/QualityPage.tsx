import { useState, useEffect, useRef } from "react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { qualityApi } from "../api/quality";
import { imagesApi } from "../api/images";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import StyleReferencePicker from "../components/quality/StyleReferencePicker";

const SCORING_OPTIONS = [
  { key: "aesthetic", label: "Aesthetic score · LAION", desc: "CLIP-based aesthetic predictor (1–10). Trained on human ratings.", vram: "2.1 GB" },
  { key: "technical", label: "Technical · OpenCV", desc: "Blur, noise, near-uniform, color richness, dHash duplicates.", vram: "CPU" },
  { key: "watermark", label: "Watermark detection", desc: "CLIP zero-shot classification for text overlays and logos.", vram: "2.1 GB" },
  { key: "embeddings", label: "Style embeddings · CLIP", desc: "Required for the style-similarity workflow below.", vram: "2.1 GB" },
  { key: "dino", label: "DINOv2 embeddings", desc: "Object-aware embedding. Can be used alone or alongside CLIP for style similarity.", vram: "1.2 GB" },
  { key: "dino_layers", label: "DINOv2 per-layer embeds", desc: "Stores all 12 transformer layer CLS tokens. Enables per-layer style similarity.", vram: "1.2 GB" },
];

export default function QualityPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [runAesthetic, setRunAesthetic] = useState(true);
  const [runTechnical, setRunTechnical] = useState(true);
  const [runWatermark, setRunWatermark] = useState(false);
  const [runEmbeddings, setRunEmbeddings] = useState(false);
  const [runDino, setRunDino] = useState(false);
  const [runDinoLayers, setRunDinoLayers] = useState(false);
  const [showStyleSection, setShowStyleSection] = useState(false);
  const [selectedRefIds, setSelectedRefIds] = useState<Set<string>>(new Set());
  const [externalRefFiles, setExternalRefFiles] = useState<File[]>([]);
  const [embeddingType, setEmbeddingType] = useState<"clip" | "dino" | "combined">("clip");
  const [dinoLayer, setDinoLayer] = useState<number | "all" | null>("all");

  useJobSSE(activeJobId);
  const jobProgress = useJobStore((s) => s.activeJobs.get(activeJobId ?? ""));

  const embeddingTypeInitialized = useRef(false);
  useEffect(() => {
    if (!embeddingTypeInitialized.current) { embeddingTypeInitialized.current = true; return; }
    if (embeddingType === "clip") setDinoLayer(null);
  }, [embeddingType]);

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

  const scoreMutation = useMutation({
    mutationFn: () =>
      qualityApi.score({
        dataset_id: datasetId!,
        run_aesthetic: runAesthetic,
        run_technical: runTechnical,
        run_watermark: runWatermark,
        run_embeddings: runEmbeddings,
        run_dino: runDino,
        run_dino_layers: runDino && runDinoLayers,
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
      const isAllLayers = dinoLayer === "all";
      let apiType: "clip" | "dino" | "combined" | "dino_all_layers" | "combined_all_layers" = effectiveType as typeof apiType;
      if (effectiveType === "dino" && isAllLayers) apiType = "dino_all_layers";
      else if (effectiveType === "combined" && isAllLayers) apiType = "combined_all_layers";
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
      toast.success(`Style similarity scored for ${data.updated} images`);
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : "Style similarity scoring failed");
    },
  });

  const toggleRef = (id: string) => {
    setSelectedRefIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const dupGroups = duplicates?.groups ?? [];
  const isRunning = scoreMutation.isPending || jobProgress?.status === "running";
  const checkMap: Record<string, [boolean, (v: boolean) => void]> = {
    aesthetic: [runAesthetic, setRunAesthetic],
    technical: [runTechnical, setRunTechnical],
    watermark: [runWatermark, setRunWatermark],
    embeddings: [runEmbeddings, setRunEmbeddings],
    dino: [runDino, setRunDino],
    dino_layers: [runDinoLayers, setRunDinoLayers],
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
                  <option value={1}>Layer 1 — Low-level color &amp; gradients</option>
                  <option value={2}>Layer 2 — Edges &amp; corners</option>
                  <option value={3}>Layer 3 — Local texture orientations</option>
                  <option value={4}>Layer 4 — Texture patterns &amp; simple shapes</option>
                  <option value={5}>Layer 5 — Object part emergence</option>
                  <option value={6}>Layer 6 — Region boundaries &amp; contours</option>
                  <option value={7}>Layer 7 — Complex textures &amp; patterns</option>
                  <option value={8}>Layer 8 — Higher-level object parts</option>
                  <option value={9}>Layer 9 — Object &amp; shape representations</option>
                  <option value={10}>Layer 10 — Semantic object features</option>
                  <option value={11}>Layer 11 — Abstract semantic content</option>
                  <option value={12}>Layer 12 — Global semantics (Final, default)</option>
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
            {dupGroups.map((group, gi) => (
              <div key={gi} style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 14, alignItems: "center", padding: 12, background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
                <div>
                  <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 8 }}>{group.length} similar images · perceptual hash distance &lt; 6</div>
                  <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                    {group.map((img) => (
                      <div key={img.id} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                        <img src={imagesApi.thumbnailUrl(img.id)} alt={img.filename} style={{ width: 64, height: 64, objectFit: "cover", borderRadius: "var(--r-sm)", border: "1px solid var(--line-2)" }} />
                        <span className="mono" style={{ fontSize: 10, color: "var(--fg-dim)", textAlign: "center", maxWidth: 64, overflow: "hidden", textOverflow: "ellipsis" }}>{img.filename}</span>
                        {img.aesthetic_score != null && <span className="mono" style={{ fontSize: 11, color: "var(--good)" }}>{img.aesthetic_score.toFixed(1)}</span>}
                      </div>
                    ))}
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <button className="btn sm primary" onClick={() => {
                    const best = [...group].sort((a, b) => (b.aesthetic_score ?? 0) - (a.aesthetic_score ?? 0));
                    resolveMutation.mutate({ keep: [best[0].id], del: best.slice(1).map((i) => i.id) });
                  }}>Keep best</button>
                  <button className="btn sm" onClick={() => resolveMutation.mutate({ keep: [group[0].id], del: group.slice(1).map((i) => i.id) })}>Keep first</button>
                  <button className="btn sm ghost">Review</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
