import { useState, useEffect } from "react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { captioningApi } from "../api/captioning";
import { detectionApi } from "../api/detection";
import { jobsApi } from "../api/jobs";
import { datasetsApi } from "../api/datasets";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import { useSelectionStore } from "../store/selectionStore";
import { usePresetsStore } from "../store/promptPresetsStore";
import ResolutionPicker from "../components/caption/ResolutionPicker";
import type { ModelInfo, OllamaModel } from "../types";
import { STYLE_LABELS, modelType } from "../constants/captionStyles";
import { FLAG_OPTIONS } from "../constants/flags";

type Scope = "uncaptioned" | "selected" | "all";

export default function CaptioningPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const { selectedIds, count: selCount } = useSelectionStore();
  const { presets, save: savePreset, remove: removePreset } = usePresetsStore();

  const [selectedModel, setSelectedModel] = useState("");
  const [ollamaModelInput, setOllamaModelInput] = useState("");
  const [style, setStyle] = useState("detailed");
  const [customPrompt, setCustomPrompt] = useState("");
  const [targetWidth, setTargetWidth] = useState<number | null>(null);
  const [targetHeight, setTargetHeight] = useState<number | null>(null);
  const [scope, setScope] = useState<Scope>("uncaptioned");
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>(undefined);
  const [minAestheticScore, setMinAestheticScore] = useState("");
  const [excludeFlags, setExcludeFlags] = useState<Set<string>>(new Set());
  const [stripRefusals, setStripRefusals] = useState(true);
  const [saveBackup, setSaveBackup] = useState(false);
  const [renameOnCaption, setRenameOnCaption] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [savingPreset, setSavingPreset] = useState(false);
  const [presetName, setPresetName] = useState("");
  const [detectTask, setDetectTask] = useState("<OD>");
  const [detectPrompt, setDetectPrompt] = useState("");
  const [detectUseCaptions, setDetectUseCaptions] = useState(false);
  const [detectOverwrite, setDetectOverwrite] = useState(true);
  const [detectJobId, setDetectJobId] = useState<string | null>(null);

  // Scan the global job store (fed by TopBar's useAllJobsSSE) to detect caption
  // jobs started before this component mounted (e.g. user navigated back).
  const allActiveJobs = useJobStore((s) => s.activeJobs);
  const globalCaptionJob = !activeJobId
    ? Array.from(allActiveJobs.values()).find(
        (j) => j.job_type === "caption" && j.status === "running"
      )
    : undefined;
  const effectiveJobId = activeJobId ?? globalCaptionJob?.job_id ?? null;

  useJobSSE(effectiveJobId);
  useJobSSE(detectJobId);
  const jobProgress = useJobStore((s) => s.activeJobs.get(effectiveJobId ?? ""));
  const detectJobProgress = useJobStore((s) => s.activeJobs.get(detectJobId ?? ""));

  const { data: modelsData, isLoading } = useQuery({
    queryKey: ["captioning-models"],
    queryFn: captioningApi.models,
  });
  const { data: dataset } = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => datasetsApi.get(datasetId!),
    enabled: !!datasetId,
  });
  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  const localModels = (modelsData?.local_models ?? []) as ModelInfo[];
  const ollamaModels = (modelsData?.ollama_models ?? []) as OllamaModel[];

  const selectedModelType = modelType(selectedModel);
  const availableStyles = selectedModelType ? (STYLE_LABELS[selectedModelType] ?? []) : [];

  const unloadMutation = useMutation({
    mutationFn: (modelId: string) => captioningApi.unloadModel(modelId),
    onSuccess: () => toast.success("Model unloaded"),
  });

  const runMutation = useMutation({
    mutationFn: () => captioningApi.run({
      dataset_id: datasetId!,
      model: selectedModel,
      style,
      overwrite: scope === "all",
      custom_prompt: customPrompt,
      image_ids: scope === "selected" ? [...selectedIds] : undefined,
      subfolder: scope !== "selected" ? activeSubfolder : undefined,
      ...(targetWidth && targetHeight ? { target_width: targetWidth, target_height: targetHeight } : {}),
      strip_refusals: stripRefusals,
      save_backup: saveBackup,
      rename_on_caption: renameOnCaption,
      min_aesthetic_score: minAestheticScore !== "" ? parseFloat(minAestheticScore) : undefined,
      exclude_flags: excludeFlags.size > 0 ? [...excludeFlags] : undefined,
    }),
    onSuccess: (data) => {
      if (data.job_id) {
        setActiveJobId(data.job_id);
        toast.success(`Captioning started — ${data.total} images queued`);
        qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
      } else {
        toast("No images to caption");
      }
    },
    onError: () => toast.error("Failed to start captioning"),
  });

  const cancelMutation = useMutation({
    mutationFn: () => jobsApi.cancel(effectiveJobId!),
    onSuccess: () => toast.success("Captioning stopped"),
    onError: () => toast.error("Failed to stop captioning"),
  });

  const detectMutation = useMutation({
    mutationFn: () =>
      detectionApi.run({
        dataset_id: datasetId!,
        image_ids: scope === "selected" ? [...selectedIds] : undefined,
        model: selectedModel,
        task: detectTask,
        custom_prompt: detectUseCaptions ? "" : detectPrompt,
        use_caption_as_prompt: detectUseCaptions,
        overwrite: detectOverwrite,
      }),
    onSuccess: (data) => {
      if (data.job_id) {
        setDetectJobId(data.job_id);
        toast.success("Detection started");
      } else {
        toast("No images to process");
      }
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg ?? "Failed to start detection");
    },
  });

  useEffect(() => {
    if (jobProgress?.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
    }
  }, [jobProgress?.status, datasetId]);

  useEffect(() => {
    if (!detectJobId || !detectJobProgress) return;
    if (detectJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setDetectJobId(null);
      toast.success("Detection complete");
    } else if (detectJobProgress.status === "failed") {
      setDetectJobId(null);
      toast.error("Detection failed");
    }
  }, [detectJobProgress?.status, detectJobId, datasetId, qc]);

  useEffect(() => {
    if (jobProgress?.status === "running" && (jobProgress?.done ?? 0) > 0) {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
    }
  }, [jobProgress?.done, datasetId]);

  const uncaptioned = (dataset?.image_count ?? 0) - (dataset?.captioned_count ?? 0);
  const isDone = jobProgress?.status === "completed";
  const isFailed = jobProgress?.status === "failed";
  const isCancelled = jobProgress?.status === "cancelled";
  const canStop = !!effectiveJobId && !isDone && !isFailed && !isCancelled;

  function handleStop() {
    cancelMutation.mutate();
  }

  function handleSavePreset() {
    if (!presetName.trim()) return;
    savePreset({ name: presetName.trim(), model: selectedModel, style, prompt: customPrompt });
    setPresetName("");
    setSavingPreset(false);
    toast.success("Preset saved");
  }

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h">
        <div>
          <h1>Captioning</h1>
          <p>Generate captions and tags for training. Long jobs run in the background; close this page anytime.</p>
        </div>
        <div className="phactions">
          {canStop && (
            <button
              className="btn danger"
              onClick={handleStop}
              disabled={cancelMutation.isPending}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
                <rect x="3" y="3" width="10" height="10" rx="1.5" fill="currentColor" stroke="none"/>
              </svg>
              {cancelMutation.isPending ? "Stopping…" : "Stop"}
            </button>
          )}
          <button
            className="btn primary"
            onClick={() => runMutation.mutate()}
            disabled={!selectedModel || runMutation.isPending || canStop}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M4 3l8 5-8 5V3z"/>
            </svg>
            {runMutation.isPending ? "Starting…" : "Run captioning"}
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16, alignItems: "start" }}>
        {/* Left: Configuration */}
        <div className="panel">
          <div className="panel-h">
            <h3>Configuration</h3>
            <div style={{ flex: 1 }} />
            <span className="badge solid mono">{dataset?.captioned_count ?? 0} / {dataset?.image_count ?? 0} captioned</span>
          </div>
          <div style={{ padding: "4px 22px" }}>

            {/* Model */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Model</h4>
                <p>Vision-language model that generates the caption.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {isLoading && <span style={{ color: "var(--fg-mute)", fontSize: 12 }}>Loading models…</span>}

                {localModels.map((m) => (
                  <div
                    key={m.id}
                    className={`model-row${selectedModel === m.id ? " sel" : ""}`}
                    onClick={() => { setSelectedModel(m.id); setOllamaModelInput(""); setStyle("detailed"); }}
                  >
                    <div className="ind" />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="mr-name">{m.name}</div>
                      <div className="mr-desc">{m.id.startsWith("florence2") ? "Microsoft · best for descriptive prose" : "Google · accepts custom prompt · requires HF token"}</div>
                    </div>
                    {m.loaded && (
                      <button
                        className="btn ghost sm"
                        style={{ fontSize: 10.5 }}
                        onClick={(e) => { e.stopPropagation(); unloadMutation.mutate(m.id); }}
                      >
                        Unload
                      </button>
                    )}
                    <span className="mr-vram">{m.vram_mb ? `${(m.vram_mb / 1024).toFixed(1)} GB` : "—"}</span>
                  </div>
                ))}

                <div style={{ fontSize: 10.5, color: "var(--fg-dim)", letterSpacing: ".04em", textTransform: "uppercase", padding: "6px 0 2px", marginTop: 2 }}>Ollama</div>
                <div style={{ display: "flex", gap: 8 }}>
                  <select
                    className="select"
                    style={{ flex: 1 }}
                    value={ollamaModels.some((m) => m.id === selectedModel) ? selectedModel : ""}
                    onChange={(e) => {
                      if (e.target.value) {
                        setSelectedModel(e.target.value);
                        setOllamaModelInput("");
                        setStyle("detailed");
                      }
                    }}
                  >
                    <option value="">— select a model —</option>
                    {ollamaModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}{m.size_mb > 0 ? ` (${(m.size_mb / 1024).toFixed(1)} GB)` : ""}
                      </option>
                    ))}
                  </select>
                  <input
                    className="input"
                    placeholder="or type name…"
                    value={ollamaModelInput}
                    onChange={(e) => {
                      setOllamaModelInput(e.target.value);
                      setSelectedModel(e.target.value ? `ollama:${e.target.value}` : "");
                      setStyle("detailed");
                    }}
                    style={{ width: 140 }}
                  />
                </div>
              </div>
            </div>

            {/* Style */}
            {availableStyles.length > 0 && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Style</h4>
                  <p>Output format for the generated caption.</p>
                </div>
                <div className="row-flex">
                  {availableStyles.map((s) => (
                    <button key={s} className={`btn sm${style === s ? " primary" : ""}`} onClick={() => setStyle(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Prompt */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Prompt</h4>
                <p>Override the default model prompt. Used by PaliGemma and Ollama.</p>
              </div>
              <textarea
                className="input"
                style={{ height: 80 }}
                value={customPrompt}
                onChange={(e) => setCustomPrompt(e.target.value)}
                placeholder="Leave blank to use the style preset prompt…"
              />
            </div>

            {/* Presets */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Presets</h4>
                <p>Saved prompt &amp; style configurations.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {presets.length === 0 ? (
                  <p style={{ fontSize: 12, color: "var(--fg-dim)", margin: 0 }}>
                    No presets saved yet. Enter a prompt above and click Save.
                  </p>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {presets.map((p) => (
                      <div key={p.id} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <button
                          className="btn ghost sm"
                          style={{ flex: 1, justifyContent: "flex-start", textAlign: "left" }}
                          onClick={() => {
                            setCustomPrompt(p.prompt);
                            setStyle(p.style);
                            toast.success(`Loaded "${p.name}"`);
                          }}
                        >
                          <span style={{ fontWeight: 500 }}>{p.name}</span>
                          <span style={{ color: "var(--fg-dim)", fontSize: 10.5, marginLeft: 6 }}>{p.style}</span>
                        </button>
                        <button
                          className="btn ghost sm"
                          style={{ color: "var(--bad)", flexShrink: 0 }}
                          onClick={() => removePreset(p.id)}
                          title="Delete preset"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                {savingPreset ? (
                  <div style={{ display: "flex", gap: 6 }}>
                    <input
                      className="input"
                      placeholder="Preset name…"
                      value={presetName}
                      onChange={(e) => setPresetName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSavePreset();
                        if (e.key === "Escape") setSavingPreset(false);
                      }}
                      autoFocus
                      style={{ flex: 1 }}
                    />
                    <button className="btn sm primary" disabled={!presetName.trim()} onClick={handleSavePreset}>OK</button>
                    <button className="btn sm ghost" onClick={() => setSavingPreset(false)}>Cancel</button>
                  </div>
                ) : (
                  <button
                    className="btn ghost sm"
                    onClick={() => { setPresetName(""); setSavingPreset(true); }}
                    style={{ alignSelf: "flex-start" }}
                  >
                    + Save current as preset
                  </button>
                )}
              </div>
            </div>

            {/* Target resolution */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Target resolution</h4>
                <p>Center-crop & resize to the resolution your trainer uses, so captions describe what the model actually sees.</p>
              </div>
              <ResolutionPicker
                targetWidth={targetWidth}
                targetHeight={targetHeight}
                onChange={(w, h) => { setTargetWidth(w); setTargetHeight(h); }}
              />
            </div>

            {/* Scope */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Scope</h4>
                <p>Which images to caption.</p>
              </div>
              <div className="row-flex">
                {([
                  { value: "uncaptioned", label: `Uncaptioned only`, sub: uncaptioned },
                  { value: "selected", label: "Selected", sub: selCount },
                  { value: "all", label: "Re-caption all", sub: null },
                ] as const).map((opt) => (
                  <label key={opt.value} className="row-flex" style={{ gap: 6, cursor: "pointer" }}>
                    <input type="radio" name="scope" checked={scope === opt.value} onChange={() => setScope(opt.value)} />
                    <span style={{ fontSize: 12.5 }}>
                      {opt.label}
                      {opt.sub !== null && <span className="mono" style={{ color: "var(--fg-dim)", marginLeft: 4 }}>{opt.sub}</span>}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {/* Subfolder */}
            {subfolders.length > 0 && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Subfolder</h4>
                  <p>Limit captioning to a specific subfolder. Ignored when scope is "Selected".</p>
                </div>
                <select
                  className="select"
                  value={activeSubfolder ?? ""}
                  onChange={(e) => setActiveSubfolder(e.target.value || undefined)}
                  disabled={canStop}
                >
                  <option value="">All subfolders</option>
                  {subfolders.map((sf) => (
                    <option key={sf.path} value={sf.path}>
                      {sf.path || "(root)"} ({sf.image_count})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Quality Filters */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Quality filters</h4>
                <p>Skip images that don't meet quality criteria.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <label style={{ fontSize: 12.5, color: "var(--fg-mute)", whiteSpace: "nowrap" }}>
                    Min aesthetic score
                  </label>
                  <input
                    type="number"
                    className="input"
                    style={{ width: 80 }}
                    placeholder="None"
                    min={0}
                    max={10}
                    step={0.5}
                    value={minAestheticScore}
                    onChange={(e) => setMinAestheticScore(e.target.value)}
                    disabled={canStop}
                  />
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {FLAG_OPTIONS.map((opt) => (
                    <label key={opt.key} className="row-flex" style={{ gap: 6, cursor: "pointer", fontSize: 12.5 }}>
                      <input
                        type="checkbox"
                        className="checkbox"
                        checked={excludeFlags.has(opt.key)}
                        onChange={(e) =>
                          setExcludeFlags((prev) => {
                            const next = new Set(prev);
                            e.target.checked ? next.add(opt.key) : next.delete(opt.key);
                            return next;
                          })
                        }
                        disabled={canStop}
                      />
                      <span>Skip {opt.label.toLowerCase()}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            {/* Options */}
            <div className="form-row">
              <div className="lbl-col">
                <h4>Options</h4>
                <p>Post-processing applied to each caption.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {[
                  { label: "Strip refusal phrases & identity guesses", val: stripRefusals, set: setStripRefusals },
                  { label: <>Save backup of previous caption to <span className="mono">.caption.bak</span></>, val: saveBackup, set: setSaveBackup },
                  { label: "Rename files using subfolder name and increment", val: renameOnCaption, set: setRenameOnCaption },
                ].map((opt, i) => (
                  <label key={i} className="row-flex" style={{ gap: 8, cursor: "pointer" }}>
                    <input type="checkbox" className="checkbox" checked={opt.val} onChange={(e) => opt.set(e.target.checked)} />
                    <span style={{ fontSize: 12.5 }}>{opt.label}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Detection — Florence-2 only */}
            {selectedModel.startsWith("florence2") && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Object Detection</h4>
                  <p>Run bounding-box detection using the same Florence-2 model. Scope follows the setting above.</p>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <select
                    className="select"
                    value={detectTask}
                    onChange={(e) => { setDetectTask(e.target.value); setDetectPrompt(""); setDetectUseCaptions(false); }}
                  >
                    <option value="<OD>">Object Detection (auto-detect everything)</option>
                    <option value="<CAPTION_TO_PHRASE_GROUNDING>">Grounded Caption (draw boxes around phrases)</option>
                  </select>
                  {detectTask === "<CAPTION_TO_PHRASE_GROUNDING>" && (
                    <>
                      <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12.5 }}>
                        <input
                          type="checkbox"
                          className="checkbox"
                          checked={detectUseCaptions}
                          onChange={(e) => { setDetectUseCaptions(e.target.checked); setDetectPrompt(""); }}
                        />
                        Use each image's existing caption as prompt
                      </label>
                      {!detectUseCaptions && (
                        <input
                          className="input"
                          placeholder="Caption to ground, e.g. a cat sitting on a mat"
                          value={detectPrompt}
                          onChange={(e) => setDetectPrompt(e.target.value)}
                        />
                      )}
                      {detectUseCaptions && (
                        <p style={{ fontSize: 11, color: "var(--fg-mute)", margin: 0 }}>
                          Images without a caption will be skipped.
                        </p>
                      )}
                    </>
                  )}
                  {detectJobId && detectJobProgress && (
                    <div>
                      <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                        <div style={{ height: "100%", width: `${detectJobProgress.percent ?? 0}%`, background: "var(--accent)", transition: "width .4s" }} />
                      </div>
                      <p style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 4 }}>
                        {detectJobProgress.message || "Detecting…"}
                      </p>
                    </div>
                  )}
                  <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12.5 }}>
                    <input
                      type="checkbox"
                      className="checkbox"
                      checked={detectOverwrite}
                      onChange={(e) => setDetectOverwrite(e.target.checked)}
                    />
                    Overwrite existing detections
                  </label>
                  <button
                    className="btn ghost sm"
                    style={{ alignSelf: "flex-start" }}
                    onClick={() => detectMutation.mutate()}
                    disabled={
                      detectMutation.isPending ||
                      !!detectJobId ||
                      (detectTask === "<CAPTION_TO_PHRASE_GROUNDING>" && !detectUseCaptions && !detectPrompt.trim())
                    }
                  >
                    {detectJobId ? "Running…" : "Run Detection"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right: Live progress */}
        <div className="panel">
          <div className="panel-h"><h3>Live progress</h3></div>
          <div className="panel-b">
            {!jobProgress ? (
              <div className="empty-state" style={{ padding: "40px 20px" }}>
                <svg width="32" height="32" viewBox="0 0 16 16" fill="none" stroke="var(--fg-soft)" strokeWidth="1.2">
                  <path d="M4 3l8 5-8 5V3z"/>
                </svg>
                <span style={{ color: "var(--fg-dim)", fontSize: 12 }}>Run captioning to see progress here</span>
              </div>
            ) : (
              <>
                <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 10, marginBottom: 14 }}>
                  <div>
                    <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-.02em", fontFamily: "Geist Mono, monospace" }}>
                      {jobProgress.done ?? 0}
                      <span style={{ color: "var(--fg-dim)", fontSize: 18 }}>/{jobProgress.total ?? 0}</span>
                    </div>
                    <div style={{ color: "var(--fg-mute)", fontSize: 12, marginTop: 2 }}>
                      {isDone ? "Complete" : isFailed ? "Failed" : isCancelled ? "Stopped" : "Processing…"}
                    </div>
                  </div>
                  <span className={`badge dot ${isDone ? "good" : isFailed || isCancelled ? "bad" : "info"}`}>
                    {isDone ? "Done" : isFailed ? "Failed" : isCancelled ? "Stopped" : "Running"}
                  </span>
                </div>

                <div style={{ height: 5, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${jobProgress.percent ?? 0}%`, background: "linear-gradient(90deg, var(--accent-2), var(--accent))", transition: "width .4s" }} />
                </div>

                <div className="divider" />

                <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, marginBottom: 14 }}>
                  {(jobProgress.throughput_ips ?? 0) > 0 && (
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--fg-mute)" }}>Throughput</span>
                      <span className="mono">{jobProgress.throughput_ips!.toFixed(2)} img/s</span>
                    </div>
                  )}
                  {(jobProgress.vram_used_mb ?? 0) > 0 ? (
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--fg-mute)" }}>VRAM</span>
                      <span className="mono">{Math.round(jobProgress.vram_used_mb! / 1024 * 10) / 10} GB used</span>
                    </div>
                  ) : null}
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "var(--fg-mute)" }}>Last image</span>
                    <span className="mono" style={{ color: "var(--fg-dim)", maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {jobProgress.current_item || "—"}
                    </span>
                  </div>
                </div>

                {jobProgress.message && !isDone && !isFailed && !isCancelled && (
                  <div style={{ padding: "10px 12px", background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)", fontSize: 12, color: "var(--fg)", marginBottom: 12 }}>
                    {jobProgress.message}
                  </div>
                )}

                {/* Stop button inside progress panel */}
                {canStop && (
                  <button
                    className="btn danger"
                    style={{ width: "100%" }}
                    onClick={handleStop}
                    disabled={cancelMutation.isPending}
                  >
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
                      <rect x="3" y="3" width="10" height="10" rx="1.5"/>
                    </svg>
                    {cancelMutation.isPending ? "Stopping…" : "Stop captioning"}
                  </button>
                )}

                {isDone && <p style={{ color: "var(--good)", fontSize: 12, marginTop: 8 }}>✓ Captioning complete</p>}
                {isFailed && <p style={{ color: "var(--bad)", fontSize: 12, marginTop: 8 }}>✗ Captioning failed</p>}
                {isCancelled && <p style={{ color: "var(--warn)", fontSize: 12, marginTop: 8 }}>⏹ Captioning stopped</p>}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
