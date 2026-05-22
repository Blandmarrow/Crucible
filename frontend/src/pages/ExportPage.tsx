import { useState, useEffect } from "react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { exportApi } from "../api/export";
import { datasetsApi } from "../api/datasets";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import type { SubfolderInfo } from "../types";
import DirPickerModal from "../components/common/DirPickerModal";
import { FolderOpen } from "lucide-react";
import { FLAG_OPTIONS } from "../constants/flags";

type Format = "kohya" | "aitoolkit" | "plain";
type CaptionFmt = "txt" | "caption" | "jsonl";
type ResizeTo = number | null;

const FORMAT_LABELS: Record<Format, string> = {
  kohya: "kohya",
  aitoolkit: "ai-toolkit",
  plain: "plain folder",
};

export default function ExportPage() {
  const datasetId = usePaneDatasetId();
  const [format, setFormat] = useState<Format>("kohya");
  const [captionFmt, setCaptionFmt] = useState<CaptionFmt>("txt");
  const [outputDir, setOutputDir] = useState("");
  const [nRepeats, setNRepeats] = useState(10);
  const [conceptToken, setConceptToken] = useState("concept");
  const [outputImgFmt, setOutputImgFmt] = useState("original");
  const [resizeTo, setResizeTo] = useState<ResizeTo>(null);
  const [customResize, setCustomResize] = useState(false);
  const [customResizeVal, setCustomResizeVal] = useState("");
  const [dirPickerOpen, setDirPickerOpen] = useState(false);

  // Filters
  const [filterAesthetic, setFilterAesthetic] = useState(false);
  const [aestheticMin, setAestheticMin] = useState(5.0);
  const [filterCaptioned, setFilterCaptioned] = useState(true);
  const [excludeFlags, setExcludeFlags] = useState<Set<string>>(new Set(["has_watermark"]));
  const [filterStyleSim, setFilterStyleSim] = useState(false);
  const [styleSimMin, setStyleSimMin] = useState(0.5);
  const [subfolderFilterActive, setSubfolderFilterActive] = useState(false);
  const [selectedSubfolders, setSelectedSubfolders] = useState<Set<string>>(new Set());

  const [stripMetadata, setStripMetadata] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  useJobSSE(activeJobId);
  const jobProgress = useJobStore((s) => s.activeJobs.get(activeJobId ?? ""));

  const { data: subfolders = [] } = useQuery<SubfolderInfo[]>({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  // Debounced filter params for preview query
  const [debouncedFilters, setDebouncedFilters] = useState({
    aesthetic_min: null as number | null,
    captioned_only: filterCaptioned,
    exclude_flags: "has_watermark",
    style_sim_min: null as number | null,
    subfolders: null as string[] | null,
  });

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedFilters({
        aesthetic_min: filterAesthetic ? aestheticMin : null,
        captioned_only: filterCaptioned,
        exclude_flags: [...excludeFlags].join(","),
        style_sim_min: filterStyleSim ? styleSimMin : null,
        subfolders: subfolderFilterActive ? [...selectedSubfolders] : null,
      });
    }, 350);
    return () => clearTimeout(t);
  }, [filterAesthetic, aestheticMin, filterCaptioned, excludeFlags, filterStyleSim, styleSimMin, subfolderFilterActive, selectedSubfolders]);

  const { data: preview } = useQuery({
    queryKey: ["export-preview", datasetId, debouncedFilters],
    queryFn: () => exportApi.preview(datasetId!, debouncedFilters),
    enabled: !!datasetId,
  });

  const buildFilters = () => ({
    caption_format: captionFmt,
    resize_to: customResize ? (parseInt(customResizeVal, 10) || null) : resizeTo,
    aesthetic_min: filterAesthetic ? aestheticMin : null,
    captioned_only: filterCaptioned,
    exclude_flags: [...excludeFlags].join(","),
    style_sim_min: filterStyleSim ? styleSimMin : null,
    subfolders: subfolderFilterActive ? [...selectedSubfolders] : null,
    strip_metadata: stripMetadata,
  });

  const exportMutation = useMutation({
    mutationFn: () => {
      const filters = buildFilters();
      const common = { dataset_id: datasetId!, output_dir: outputDir, output_format: outputImgFmt, ...filters };
      if (format === "kohya") return exportApi.kohya({ ...common, n_repeats: nRepeats, concept_token: conceptToken });
      if (format === "aitoolkit") return exportApi.aitoolkit({ ...common, concept_name: conceptToken });
      return exportApi.plain(common);
    },
    onSuccess: (data) => { setActiveJobId(data.job_id); toast.success("Export started"); },
    onError: () => toast.error("Export failed"),
  });

  const treePreview = () => {
    const base = outputDir || "output_dir";
    switch (format) {
      case "kohya":     return `${base}/\n  ${nRepeats}_${conceptToken}/\n    image.png\n    image.txt`;
      case "aitoolkit": return `${base}/\n  ${conceptToken}/\n    image.jpg\n    image.txt`;
      case "plain":     return `${base}/\n  images/\n    image.png\n  captions.jsonl\n  tags.csv`;
    }
  };

  const toggleFlag = (key: string) => setExcludeFlags((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const isRunning = exportMutation.isPending || jobProgress?.status === "running";
  const isDone = jobProgress?.status === "completed";
  const showConcept = format === "kohya" || format === "aitoolkit";

  const exclusionRows = [
    { label: "Low aesthetic", count: preview?.excluded_low_aesthetic, show: filterAesthetic },
    { label: "No caption",    count: preview?.excluded_uncaptioned,   show: filterCaptioned },
    { label: "Flagged",       count: preview?.excluded_flagged,       show: excludeFlags.size > 0 },
    { label: "Low style sim", count: preview?.excluded_style_sim,     show: filterStyleSim },
  ].filter((r) => r.show);

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h" style={{ marginBottom: 20 }}>
        <div>
          <h1>Export</h1>
          <p>Package dataset into a training-ready format.</p>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 16, alignItems: "start" }}>
        {/* Left: Configuration */}
        <div className="panel">
          <div className="panel-h"><h3>Configuration</h3></div>
          <div style={{ padding: "4px 22px" }}>

            {/* Format */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Format</h4>
                <p>Training framework target.</p>
              </div>
              <div>
                <div className="row-flex" style={{ flexWrap: "wrap" }}>
                  {(["kohya", "aitoolkit", "plain"] as Format[]).map((f) => (
                    <button key={f} className={`btn sm${format === f ? " primary" : ""}`} onClick={() => setFormat(f)}>
                      {FORMAT_LABELS[f]}
                    </button>
                  ))}
                </div>
                <pre style={{ marginTop: 10, padding: "10px 12px", background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)", fontSize: 11.5, color: "var(--fg-mute)", fontFamily: "Geist Mono, monospace", lineHeight: 1.8, overflowX: "auto", whiteSpace: "pre" }}>
                  {treePreview()}
                </pre>
              </div>
            </div>

            {/* Caption file — not shown for plain (always jsonl there) */}
            {format !== "plain" && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Caption file</h4>
                  <p>How captions are written to disk.</p>
                </div>
                <div className="row-flex">
                  {([["txt", ".txt sidecar"], ["caption", ".caption sidecar"], ["jsonl", "JSONL manifest"]] as [CaptionFmt, string][]).map(([v, label]) => (
                    <button key={v} className={`btn sm${captionFmt === v ? " primary" : ""}`} onClick={() => setCaptionFmt(v)}>{label}</button>
                  ))}
                </div>
              </div>
            )}

            {/* Filters */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Filters</h4>
                <p>Exclude images that don't meet the criteria.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>

                {/* Aesthetic */}
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={filterAesthetic} onChange={(e) => setFilterAesthetic(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Aesthetic ≥</span>
                  <input
                    type="number" className="input" step={0.5} min={1} max={10}
                    value={aestheticMin} onChange={(e) => setAestheticMin(Number(e.target.value))}
                    disabled={!filterAesthetic} style={{ width: 64, textAlign: "center" }}
                  />
                </label>

                {/* Has caption */}
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={filterCaptioned} onChange={(e) => setFilterCaptioned(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Has caption</span>
                </label>

                {/* Per-flag checkboxes */}
                <div>
                  <div style={{ fontSize: 12.5, color: "var(--fg-mute)", marginBottom: 5 }}>Exclude flagged:</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 5, paddingLeft: 4 }}>
                    {FLAG_OPTIONS.map(({ key, label }) => (
                      <label key={key} className="row-flex" style={{ gap: 8 }}>
                        <input type="checkbox" className="checkbox" checked={excludeFlags.has(key)} onChange={() => toggleFlag(key)} />
                        <span style={{ fontSize: 12 }}>{label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                {/* Style similarity */}
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={filterStyleSim} onChange={(e) => setFilterStyleSim(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Style similarity ≥</span>
                  <input
                    type="number" className="input" step={0.05} min={0} max={1}
                    value={styleSimMin} onChange={(e) => setStyleSimMin(Number(e.target.value))}
                    disabled={!filterStyleSim} style={{ width: 64, textAlign: "center" }}
                  />
                </label>

                {/* Subfolder filter */}
                {subfolders.length > 0 && (
                  <div>
                    <label className="row-flex" style={{ gap: 8 }}>
                      <input type="checkbox" className="checkbox" checked={subfolderFilterActive}
                        onChange={(e) => setSubfolderFilterActive(e.target.checked)} />
                      <span style={{ fontSize: 12.5 }}>Limit to subfolders</span>
                    </label>
                    {subfolderFilterActive && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 5, paddingLeft: 4, marginTop: 6 }}>
                        {subfolders.map((sf) => (
                          <label key={sf.path} className="row-flex" style={{ gap: 8 }}>
                            <input
                              type="checkbox" className="checkbox"
                              checked={selectedSubfolders.has(sf.path)}
                              onChange={() => {
                                setSelectedSubfolders(prev => {
                                  const next = new Set(prev);
                                  next.has(sf.path) ? next.delete(sf.path) : next.add(sf.path);
                                  return next;
                                });
                              }}
                            />
                            <span style={{ fontSize: 12 }}>{sf.path === "" ? "(root)" : sf.path}</span>
                            <span style={{ fontSize: 11, color: "var(--fg-mute)", marginLeft: "auto" }}>{sf.image_count}</span>
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Resize */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Resize on export</h4>
                <p>
                  Scales the <strong>longest side</strong> down to the chosen pixel count.
                  Aspect ratio is preserved — no cropping. Images already smaller than the
                  target are not upscaled. Originals are never modified.
                </p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div className="row-flex">
                  {([512, 768, 1024] as number[]).map((r) => (
                    <button
                      key={r}
                      className={`btn sm${!customResize && resizeTo === r ? " primary" : ""}`}
                      onClick={() => { setResizeTo(r); setCustomResize(false); }}
                    >
                      {r}
                    </button>
                  ))}
                  <button
                    className={`btn sm${customResize ? " primary" : ""}`}
                    onClick={() => setCustomResize(true)}
                  >
                    Custom
                  </button>
                  <button
                    className={`btn sm${!customResize && resizeTo === null ? " primary" : ""}`}
                    onClick={() => { setResizeTo(null); setCustomResize(false); }}
                  >
                    No resize
                  </button>
                </div>
                {customResize && (
                  <div className="row-flex" style={{ alignItems: "center", gap: 6 }}>
                    <input
                      className="input"
                      type="number"
                      min={64}
                      max={8192}
                      placeholder="e.g. 1536"
                      value={customResizeVal}
                      style={{ width: 110 }}
                      onChange={(e) => setCustomResizeVal(e.target.value)}
                    />
                    <span style={{ color: "var(--fg-muted)", fontSize: 12 }}>px (longest side)</span>
                  </div>
                )}
              </div>
            </div>

            {/* Output dir */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Output directory</h4>
                <p>Folder where files will be written.</p>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <input className="input" style={{ flex: 1 }} placeholder="C:\training\my_dataset" value={outputDir} onChange={(e) => setOutputDir(e.target.value)} />
                <button className="btn sm" title="Browse…" onClick={() => setDirPickerOpen(true)}>
                  <FolderOpen size={14} /> Browse
                </button>
              </div>
            </div>

            {dirPickerOpen && (
              <DirPickerModal
                initialPath={outputDir}
                onConfirm={(p) => { setOutputDir(p); setDirPickerOpen(false); }}
                onCancel={() => setDirPickerOpen(false)}
              />
            )}

            {/* Concept */}
            {showConcept && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Concept</h4>
                  <p>Token or concept name used in the folder structure.</p>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  {format === "kohya" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>Repeats</span>
                      <input type="number" className="input" min={1} max={100} value={nRepeats} onChange={(e) => setNRepeats(Number(e.target.value))} style={{ width: 80 }} />
                    </div>
                  )}
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                    <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>{format === "kohya" ? "Concept token" : "Concept name"}</span>
                    <input className="input" value={conceptToken} onChange={(e) => setConceptToken(e.target.value)} />
                  </div>
                </div>
              </div>
            )}

            {/* Image format */}
            <div className="form-row" style={{ borderBottom: "none" }}>
              <div className="lbl-col">
                <h4>Image format</h4>
                <p>Convert images when copying to the export folder.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div className="row-flex">
                  {[["original", "Keep original"], ["png", "Force PNG"], ["jpeg", "Force JPEG"]].map(([v, label]) => (
                    <button key={v} className={`btn sm${outputImgFmt === v ? " primary" : ""}`} onClick={() => setOutputImgFmt(v)}>{label}</button>
                  ))}
                </div>
                <label className="row-flex" style={{ gap: 8 }}>
                  <input type="checkbox" className="checkbox" checked={stripMetadata} onChange={(e) => setStripMetadata(e.target.checked)} />
                  <span style={{ fontSize: 12.5 }}>Strip generation metadata</span>
                </label>
              </div>
            </div>
          </div>
        </div>

        {/* Right: Summary + progress + build */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Summary */}
          <div className="panel">
            <div className="panel-h"><h3>Export summary</h3></div>
            <div style={{ padding: "14px 18px" }}>
              {preview ? (
                <>
                  {/* Will export stat */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
                    <div className="stat-card" style={{ padding: "12px 14px" }}>
                      <div className="sk">Will export</div>
                      <div className="sv" style={{ fontSize: 22 }}>{preview.will_export?.toLocaleString() ?? preview.image_count?.toLocaleString()}</div>
                    </div>
                    <div className="stat-card" style={{ padding: "12px 14px" }}>
                      <div className="sk">Total images</div>
                      <div className="sv" style={{ fontSize: 22 }}>{preview.image_count?.toLocaleString()}</div>
                    </div>
                  </div>

                  {/* Exclusion breakdown */}
                  {exclusionRows.length > 0 && (
                    <div style={{ marginBottom: 14 }}>
                      <div style={{ fontSize: 10.5, color: "var(--fg-dim)", marginBottom: 6, textTransform: "uppercase", letterSpacing: ".04em" }}>Excluded by filter</div>
                      {exclusionRows.map(({ label, count }) => (
                        <div key={label} style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
                          <span style={{ color: "var(--fg-mute)" }}>{label}</span>
                          <span className="mono" style={{ color: count ? "var(--warn)" : "var(--fg-dim)" }}>{count?.toLocaleString() ?? "—"}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Sample captions */}
                  {preview.sample_files?.length > 0 && (
                    <div>
                      <div style={{ fontSize: 10.5, color: "var(--fg-dim)", marginBottom: 6, textTransform: "uppercase", letterSpacing: ".04em" }}>Sample captions</div>
                      {(preview.sample_files as { image: string; caption_preview: string }[]).map((f) => (
                        <div key={f.image} style={{ display: "flex", gap: 8, marginBottom: 5, fontSize: 11.5 }}>
                          <span className="mono" style={{ color: "var(--fg-dim)", width: 110, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.image}</span>
                          <span style={{ color: "var(--fg-mute)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {f.caption_preview || <em style={{ color: "var(--bad)" }}>no caption</em>}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ textAlign: "center", padding: "30px 0", color: "var(--fg-soft)", fontSize: 13 }}>Loading preview…</div>
              )}
            </div>
          </div>

          {/* Progress */}
          {jobProgress && (
            <div className="panel">
              <div className="panel-h">
                <h3>Progress</h3>
                <div style={{ flex: 1 }} />
                <span className={`badge dot ${isDone ? "good" : "info"}`}>{isDone ? "Done" : "Running"}</span>
              </div>
              <div style={{ padding: "14px 18px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--fg-mute)", marginBottom: 6 }}>
                  <span>{jobProgress.message || "Exporting…"}</span>
                  <span className="mono">{jobProgress.done}/{jobProgress.total}</span>
                </div>
                <div style={{ height: 5, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${jobProgress.percent ?? 0}%`, background: "linear-gradient(90deg, var(--accent-2), var(--accent))", transition: "width .4s" }} />
                </div>
                {isDone && <p style={{ color: "var(--good)", fontSize: 12, marginTop: 8 }}>✓ Export complete → {outputDir}</p>}
              </div>
            </div>
          )}

          {/* Build button */}
          <button
            className="btn primary"
            style={{ height: 38, width: "100%", justifyContent: "center" }}
            onClick={() => exportMutation.mutate()}
            disabled={!outputDir || isRunning}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M8 10V2M5 7l3 3 3-3M2.5 13.5h11"/>
            </svg>
            {isRunning ? "Exporting…" : "Build export"}
          </button>
        </div>
      </div>
    </div>
  );
}
