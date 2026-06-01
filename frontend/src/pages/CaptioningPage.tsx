import { useState, useEffect, useRef, useMemo } from "react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { captioningApi, type PipelineStep, type DelimiterMode } from "../api/captioning";
import DelimiterControls from "../components/caption/DelimiterControls";
import { detectionApi } from "../api/detection";
import { jobsApi } from "../api/jobs";
import { datasetsApi } from "../api/datasets";
import { providersApi, type ProviderOut } from "../api/providers";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import { useSelectionStore } from "../store/selectionStore";
import { usePresetsStore } from "../store/promptPresetsStore";
import ResolutionPicker from "../components/caption/ResolutionPicker";
import ModelPicker from "../components/providers/ModelPicker";
import type { ModelInfo, OllamaModel } from "../types";
import { STYLE_LABELS, modelType } from "../constants/captionStyles";
import { FLAG_OPTIONS } from "../constants/flags";

type Scope = "uncaptioned" | "selected" | "all";

interface Wd14ModelInfo { id: string; name: string; ram_mb: number; }

interface StepConfig {
  id: string;
  model: string;
  style: string;
  customPrompt: string;
  wd14Threshold: number;
  providerModelInput: string;
  delimiterMode: DelimiterMode;
  delimiterParts: string[];
  usePreviousCaption: boolean;
}

function makeStepId() { return Math.random().toString(36).slice(2); }

function resolveModelId(base: string, providerModelInput: string): string {
  if (base.startsWith("openai_compat:") && providerModelInput) {
    // base is "openai_compat:{provider_id}" — append the model name
    return `${base}:${providerModelInput}`;
  }
  return base;
}

function StepModelPicker({
  selectedModel, setSelectedModel,
  providerModelInput, setProviderModelInput,
  localModels, wd14Models, providers, ollamaModels,
  label,
}: {
  selectedModel: string;
  setSelectedModel: (v: string) => void;
  providerModelInput: string;
  setProviderModelInput: (v: string) => void;
  localModels: ModelInfo[];
  wd14Models: Wd14ModelInfo[];
  providers: ProviderOut[];
  ollamaModels: OllamaModel[];
  label?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {label && <div style={{ fontSize: 10.5, color: "var(--fg-dim)", letterSpacing: ".04em", textTransform: "uppercase", paddingBottom: 2 }}>{label}</div>}

      {localModels.map((m) => (
        <div
          key={m.id}
          className={`model-row${selectedModel === m.id ? " sel" : ""}`}
          onClick={() => { setSelectedModel(m.id); setProviderModelInput(""); }}
        >
          <div className="ind" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="mr-name">{m.name}</div>
            <div className="mr-desc">{m.id.startsWith("florence2") ? "Microsoft · best for descriptive prose" : "Google · requires HF token"}</div>
          </div>
          <span className="mr-vram">{m.vram_mb ? `${(m.vram_mb / 1024).toFixed(1)} GB` : "—"}</span>
        </div>
      ))}

      <div style={{ fontSize: 10.5, color: "var(--fg-dim)", letterSpacing: ".04em", textTransform: "uppercase", padding: "6px 0 2px", marginTop: 2 }}>Tagger</div>
      {wd14Models.map((m) => (
        <div
          key={m.id}
          className={`model-row${selectedModel === m.id ? " sel" : ""}`}
          onClick={() => { setSelectedModel(m.id); setProviderModelInput(""); }}
        >
          <div className="ind" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="mr-name">{m.name}</div>
            <div className="mr-desc">SmilingWolf · outputs booru-style tags · threshold-based</div>
          </div>
          <span className="mr-vram">{(m.ram_mb / 1024).toFixed(1)} GB</span>
        </div>
      ))}

      {(() => {
        const localProviders = providers.filter((p) => !p.is_remote);
        const cloudProviders = providers.filter((p) => p.is_remote);
        const renderProvider = (p: ProviderOut) => {
          const baseId = `openai_compat:${p.id}`;
          const isSelected = selectedModel.startsWith(baseId);
          return (
            <div key={p.id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div
                className={`model-row${isSelected ? " sel" : ""}`}
                onClick={() => { setSelectedModel(baseId); setProviderModelInput(p.default_model); }}
              >
                <div className="ind" />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="mr-name">{p.name}</div>
                  <div className="mr-desc">{p.base_url}</div>
                </div>
              </div>
              {isSelected && (
                <div style={{ marginLeft: 12 }} onClick={(e) => e.stopPropagation()}>
                  <ModelPicker
                    value={providerModelInput}
                    onChange={setProviderModelInput}
                    providerId={p.id}
                    baseUrl={p.base_url}
                    placeholder={`Model name (default: ${p.default_model || "none"})`}
                  />
                </div>
              )}
            </div>
          );
        };
        return (
          <>
            <div style={{ fontSize: 10.5, color: "var(--fg-dim)", letterSpacing: ".04em", textTransform: "uppercase", padding: "6px 0 2px", marginTop: 2 }}>Local Providers</div>
            <div style={{ display: "flex", gap: 8 }}>
              <select
                className="select"
                style={{ flex: 1 }}
                value={ollamaModels.some((m) => m.id === selectedModel) ? selectedModel : ""}
                onChange={(e) => {
                  if (e.target.value) { setSelectedModel(e.target.value); setProviderModelInput(""); }
                }}
              >
                <option value="">Ollama — select —</option>
                {ollamaModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}{m.size_mb > 0 ? ` (${(m.size_mb / 1024).toFixed(1)} GB)` : ""}
                  </option>
                ))}
              </select>
              <input
                className="input"
                placeholder="or type name…"
                value={selectedModel.startsWith("ollama:") && !ollamaModels.some((m) => m.id === selectedModel) ? selectedModel.replace("ollama:", "") : ""}
                onChange={(e) => {
                  setSelectedModel(e.target.value ? `ollama:${e.target.value}` : "");
                  setProviderModelInput("");
                }}
                style={{ width: 130 }}
              />
            </div>
            {localProviders.map(renderProvider)}
            {cloudProviders.length > 0 && (
              <>
                <div style={{ fontSize: 10.5, color: "var(--fg-dim)", letterSpacing: ".04em", textTransform: "uppercase", padding: "6px 0 2px", marginTop: 2 }}>Cloud Providers</div>
                {cloudProviders.map(renderProvider)}
              </>
            )}
          </>
        );
      })()}
    </div>
  );
}

export default function CaptioningPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const { selectedIds, datasetByImageId } = useSelectionStore();

  // Only include IDs that belong to the current dataset — global selections persist
  // across dataset navigation, so raw selectedIds may contain IDs from other datasets.
  const selectedIdsForDataset = useMemo(
    () => [...selectedIds].filter((id) => datasetByImageId.get(id) === datasetId),
    [selectedIds, datasetByImageId, datasetId],
  );
  const selCountForDataset = selectedIdsForDataset.length;
  const { presets, save: savePreset, remove: removePreset } = usePresetsStore();

  const [selectedModel, setSelectedModel] = useState("");
  const [providerModelInput, setProviderModelInput] = useState("");
  const [style, setStyle] = useState("detailed");
  const [customPrompt, setCustomPrompt] = useState("");
  const [wd14Threshold, setWd14Threshold] = useState(0.35);
  const [targetWidth, setTargetWidth] = useState<number | null>(null);
  const [targetHeight, setTargetHeight] = useState<number | null>(null);
  const [scope, setScope] = useState<Scope>("uncaptioned");
  const [delimiterMode, setDelimiterMode] = useState<DelimiterMode>("overwrite");
  const [delimiterParts, setDelimiterParts] = useState<string[]>([",", " "]);
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>(undefined);
  const [minAestheticScore, setMinAestheticScore] = useState("");
  const [excludeFlags, setExcludeFlags] = useState<Set<string>>(new Set());
  const [stripRefusals, setStripRefusals] = useState(true);
  const [saveBackup, setSaveBackup] = useState(false);
  const [renameOnCaption, setRenameOnCaption] = useState(false);
  const [jobLabel, setJobLabel] = useState("");
  const [submittedJobIds, setSubmittedJobIds] = useState<string[]>([]);
  const [savingPreset, setSavingPreset] = useState(false);
  const [presetName, setPresetName] = useState("");
  const [detectTask, setDetectTask] = useState("<OD>");
  const [detectPrompt, setDetectPrompt] = useState("");
  const [detectUseCaptions, setDetectUseCaptions] = useState(false);
  const [detectOverwrite, setDetectOverwrite] = useState(true);
  const [detectJobId, setDetectJobId] = useState<string | null>(null);

  // Pipeline additional steps
  const [additionalSteps, setAdditionalSteps] = useState<StepConfig[]>([]);

  const allActiveJobs = useJobStore((s) => s.activeJobs);

  // Track job IDs we have confirmed as terminal so jobStore TTL eviction
  // (5-min after completion) doesn't cause them to be mistaken for "pending".
  const seenTerminalJobIds = useRef(new Set<string>());

  // Oldest submitted job that's still active (running or pending-no-events).
  // Pending jobs have no SSE events yet; once they start the worker emits events.
  // Completed jobs always emit a terminal SSE event; we record that in seenTerminalJobIds
  // so eviction from the store doesn't re-treat a completed job as pending.
  const submittedActiveJobId = submittedJobIds.find((id) => {
    // Only drop an ID once the completion effect has recorded it as terminal.
    // If we checked allActiveJobs.status here, effectiveJobId would change to null
    // in the same render as status→"completed", causing jobProgress to be undefined
    // when the completion effect runs — so the gallery invalidation would never fire.
    return !seenTerminalJobIds.current.has(id);
  }) ?? null;

  // Fall back to any globally-observed caption job when we have no active submitted job.
  // Includes "pending" so the panel survives navigation (submittedJobIds resets on remount,
  // but allActiveJobs persists in Zustand and already holds the pending SSE event).
  const globalCaptionJob = submittedActiveJobId === null
    ? Array.from(allActiveJobs.values()).find(
        (j) => (j.job_type === "caption" || j.job_type === "caption_pipeline") &&
               (j.status === "running" || j.status === "pending")
      )
    : undefined;

  const effectiveJobId = submittedActiveJobId ?? globalCaptionJob?.job_id ?? null;

  // Other pending caption jobs from the persistent store — survives navigation because
  // allActiveJobs lives in Zustand (not component state) and backend emits pending SSE events.
  const otherPendingJobs = Array.from(allActiveJobs.values()).filter((j) =>
    (j.job_type === "caption" || j.job_type === "caption_pipeline") &&
    j.status === "pending" &&
    j.job_id !== effectiveJobId
  );
  const queuedCount = otherPendingJobs.length;
  // Current job is "pending" if it has no store entry yet, or its store status is "pending"
  const currentIsAlsoPending = !!effectiveJobId && (
    !allActiveJobs.has(effectiveJobId) || allActiveJobs.get(effectiveJobId)?.status === "pending"
  );

  useJobSSE(effectiveJobId);
  useJobSSE(detectJobId);
  const jobProgress = useJobStore((s) => s.activeJobs.get(effectiveJobId ?? ""));
  const detectJobProgress = useJobStore((s) => s.activeJobs.get(detectJobId ?? ""));

  const { data: modelsData, isLoading } = useQuery({
    queryKey: ["captioning-models"],
    queryFn: captioningApi.models,
  });
  const { data: providers = [] } = useQuery({
    queryKey: ["providers"],
    queryFn: providersApi.list,
    staleTime: 30_000,
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
  const wd14Models = (modelsData?.wd14_models ?? []) as Wd14ModelInfo[];

  const isWd14 = selectedModel.startsWith("wd14:");
  const isOpenAICompat = selectedModel.startsWith("openai_compat:");
  const resolvedModel = isOpenAICompat ? resolveModelId(selectedModel, providerModelInput) : selectedModel;
  const selectedModelType = modelType(resolvedModel);
  const availableStyles = isWd14 ? [] : (selectedModelType ? (STYLE_LABELS[selectedModelType] ?? []) : []);

  const selectedProvider = isOpenAICompat
    ? providers.find((p) => selectedModel === `openai_compat:${p.id}`)
    : undefined;

  const unloadMutation = useMutation({
    mutationFn: (modelId: string) => captioningApi.unloadModel(modelId),
    onSuccess: () => toast.success("Model unloaded"),
  });

  function buildStep1(): PipelineStep {
    return {
      model: resolvedModel,
      style: isWd14 ? "tags" : style,
      custom_prompt: customPrompt,
      overwrite: scope === "all",
      append_tags: true,
      strip_refusals: stripRefusals,
      wd14_threshold: wd14Threshold,
      target_width: targetWidth,
      target_height: targetHeight,
      delimiter_mode: delimiterMode,
      delimiter: delimiterParts.join(""),
    };
  }

  function buildAdditionalStep(s: StepConfig): PipelineStep {
    const isStepWd14 = s.model.startsWith("wd14:");
    const isStepOAI = s.model.startsWith("openai_compat:");
    const stepModel = isStepOAI ? resolveModelId(s.model, s.providerModelInput) : s.model;
    let custom_prompt = s.customPrompt;
    if (s.usePreviousCaption && !custom_prompt.includes("{previous_caption}")) {
      custom_prompt = custom_prompt ? `${custom_prompt}\n\n{previous_caption}` : "{previous_caption}";
    }
    return {
      model: stepModel,
      style: isStepWd14 ? "tags" : s.style,
      custom_prompt,
      overwrite: true,
      append_tags: false,
      strip_refusals: true,
      wd14_threshold: s.wd14Threshold,
      delimiter_mode: s.delimiterMode,
      delimiter: s.delimiterParts.join(""),
    };
  }

  const isPipeline = additionalSteps.length > 0;

  const runMutation = useMutation({
    mutationFn: () => {
      if (isPipeline) {
        const steps = [buildStep1(), ...additionalSteps.map(buildAdditionalStep)];
        return captioningApi.pipeline({
          dataset_id: datasetId!,
          steps,
          image_ids: scope === "selected" ? selectedIdsForDataset : undefined,
          subfolder: scope !== "selected" ? activeSubfolder : undefined,
          save_backup: saveBackup,
          rename_on_caption: renameOnCaption,
          min_aesthetic_score: minAestheticScore !== "" ? parseFloat(minAestheticScore) : undefined,
          exclude_flags: excludeFlags.size > 0 ? [...excludeFlags] : undefined,
          label: jobLabel.trim() || undefined,
        });
      }
      return captioningApi.run({
        dataset_id: datasetId!,
        model: resolvedModel,
        style: isWd14 ? "tags" : style,
        overwrite: scope !== "uncaptioned",
        custom_prompt: customPrompt,
        image_ids: scope === "selected" ? selectedIdsForDataset : undefined,
        subfolder: scope !== "selected" ? activeSubfolder : undefined,
        ...(targetWidth && targetHeight ? { target_width: targetWidth, target_height: targetHeight } : {}),
        strip_refusals: stripRefusals,
        save_backup: saveBackup,
        rename_on_caption: renameOnCaption,
        min_aesthetic_score: minAestheticScore !== "" ? parseFloat(minAestheticScore) : undefined,
        exclude_flags: excludeFlags.size > 0 ? [...excludeFlags] : undefined,
        wd14_threshold: isWd14 ? wd14Threshold : undefined,
        label: jobLabel.trim() || undefined,
        delimiter_mode: delimiterMode,
        delimiter: delimiterParts.join(""),
      });
    },
    onSuccess: (data) => {
      if (data.job_id) {
        setSubmittedJobIds((prev) => [...prev, data.job_id!]);
        toast.success(`${isPipeline ? "Pipeline" : "Captioning"} started — ${data.total} images queued`);
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
    const s = jobProgress?.status;
    if (s === "completed" || s === "failed" || s === "cancelled") {
      if (effectiveJobId) seenTerminalJobIds.current.add(effectiveJobId);
    }
    if (s === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
    }
  }, [jobProgress?.status, datasetId, effectiveJobId]);

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
      const imageId = (jobProgress as { image_id?: string }).image_id;
      if (imageId) {
        qc.invalidateQueries({ queryKey: ["caption", imageId] });
      }
    }
  }, [jobProgress?.done, datasetId]);

  const uncaptioned = (dataset?.image_count ?? 0) - (dataset?.captioned_count ?? 0);
  const isDone = jobProgress?.status === "completed";
  const isFailed = jobProgress?.status === "failed";
  const isCancelled = jobProgress?.status === "cancelled";
  const isRunning = !!effectiveJobId && !isDone && !isFailed && !isCancelled;

  function handleStop() { cancelMutation.mutate(); }

  function handleSavePreset() {
    if (!presetName.trim()) return;
    savePreset({ name: presetName.trim(), model: selectedModel, style, prompt: customPrompt });
    setPresetName("");
    setSavingPreset(false);
    toast.success("Preset saved");
  }

  // Step progress info for pipeline
  const stepIndex = (jobProgress as { step_index?: number } | undefined)?.step_index;
  const stepTotal = (jobProgress as { step_total?: number } | undefined)?.step_total;
  // Failed image count — populated by caption_summary SSE event merged into job store
  const failedCount = (jobProgress as { failed_count?: number } | undefined)?.failed_count ?? 0;

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h">
        <div>
          <h1>Captioning</h1>
          <p>Generate captions and tags for training. Long jobs run in the background; close this page anytime.</p>
        </div>
        <div className="phactions">
          {queuedCount > 0 && (
            <span className="badge info" style={{ alignSelf: "center" }}>{queuedCount} queued</span>
          )}
          <input
            className="input"
            type="text"
            placeholder="Job label (optional)"
            value={jobLabel}
            onChange={(e) => setJobLabel(e.target.value)}
            style={{ width: 200, fontSize: 12 }}
            title="Optional name shown in the job queue"
          />
          {isRunning && (
            <button className="btn danger" onClick={handleStop} disabled={cancelMutation.isPending}>
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
                <rect x="3" y="3" width="10" height="10" rx="1.5" fill="currentColor" stroke="none"/>
              </svg>
              {cancelMutation.isPending ? "Stopping…" : "Stop"}
            </button>
          )}
          <button
            className="btn primary"
            onClick={() => runMutation.mutate()}
            disabled={!selectedModel || runMutation.isPending}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M4 3l8 5-8 5V3z"/>
            </svg>
            {runMutation.isPending ? "Starting…" : isPipeline ? `Run Pipeline (${additionalSteps.length + 1} steps)` : "Run captioning"}
          </button>
        </div>
      </div>

      {selectedProvider?.is_remote && (
        <div style={{
          background: "color-mix(in srgb, var(--warn) 12%, transparent)",
          border: "1px solid color-mix(in srgb, var(--warn) 40%, transparent)",
          borderRadius: "var(--r)",
          padding: "10px 14px",
          fontSize: 12.5,
          color: "var(--warn)",
          marginBottom: 16,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}>
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M8 1L15 14H1L8 1z"/>
            <path d="M8 6v4M8 11.5v.5" strokeLinecap="round"/>
          </svg>
          Images will be sent to an external API ({selectedProvider.base_url}). Ensure you are comfortable with your data leaving this machine.
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16, alignItems: "start" }}>
        {/* Left: Configuration */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

          {/* Step 1 card */}
          <div className="panel">
            <div className="panel-h">
              <h3>{isPipeline ? "Step 1" : "Configuration"}</h3>
              <div style={{ flex: 1 }} />
              <span className="badge solid mono">{dataset?.captioned_count ?? 0} / {dataset?.image_count ?? 0} captioned</span>
            </div>
            <div style={{ padding: "4px 22px" }}>

              {/* Model */}
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Model</h4>
                  <p>Vision-language model or tagger.</p>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {isLoading && <span style={{ color: "var(--fg-mute)", fontSize: 12 }}>Loading models…</span>}
                  <StepModelPicker
                    selectedModel={selectedModel}
                    setSelectedModel={(v) => { setSelectedModel(v); setStyle("detailed"); }}
                    providerModelInput={providerModelInput}
                    setProviderModelInput={setProviderModelInput}
                    localModels={localModels}
                    wd14Models={wd14Models}
                    providers={providers}
                    ollamaModels={ollamaModels}
                  />
                  {localModels.some((m) => m.id === selectedModel && m.loaded) && (
                    <button
                      className="btn ghost sm"
                      style={{ fontSize: 10.5, alignSelf: "flex-start" }}
                      onClick={() => unloadMutation.mutate(selectedModel)}
                    >
                      Unload model
                    </button>
                  )}
                </div>
              </div>

              {/* WD14 threshold */}
              {isWd14 && (
                <div className="form-row">
                  <div className="lbl-col">
                    <h4>Tag threshold</h4>
                    <p>Minimum confidence score (0–1) for a tag to be included. 0.35 is a good default.</p>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <input
                      type="range"
                      min={0} max={1} step={0.05}
                      value={wd14Threshold}
                      onChange={(e) => setWd14Threshold(parseFloat(e.target.value))}
                      style={{ flex: 1 }}
                    />
                    <span className="mono" style={{ fontSize: 13, minWidth: 36 }}>{wd14Threshold.toFixed(2)}</span>
                  </div>
                </div>
              )}

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
              {!isWd14 && (
                <div className="form-row">
                  <div className="lbl-col">
                    <h4>Prompt</h4>
                    <p>Override the default model prompt. Used by Ollama and OpenAI-compatible models.</p>
                  </div>
                  <textarea
                    className="input"
                    style={{ height: 80 }}
                    value={customPrompt}
                    onChange={(e) => setCustomPrompt(e.target.value)}
                    placeholder="Leave blank to use the style preset prompt…"
                  />
                </div>
              )}

              {/* Presets */}
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Presets</h4>
                  <p>Saved prompt &amp; style configurations.</p>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {presets.length === 0 ? (
                    <p style={{ fontSize: 12, color: "var(--fg-dim)", margin: 0 }}>No presets saved yet.</p>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      {presets.map((p) => (
                        <div key={p.id} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                          <button
                            className="btn ghost sm"
                            style={{ flex: 1, justifyContent: "flex-start", textAlign: "left" }}
                            onClick={() => { setCustomPrompt(p.prompt); setStyle(p.style); toast.success(`Loaded "${p.name}"`); }}
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
                        onKeyDown={(e) => { if (e.key === "Enter") handleSavePreset(); if (e.key === "Escape") setSavingPreset(false); }}
                        autoFocus
                        style={{ flex: 1 }}
                      />
                      <button className="btn sm primary" disabled={!presetName.trim()} onClick={handleSavePreset}>OK</button>
                      <button className="btn sm ghost" onClick={() => setSavingPreset(false)}>Cancel</button>
                    </div>
                  ) : (
                    <button className="btn ghost sm" onClick={() => { setPresetName(""); setSavingPreset(true); }} style={{ alignSelf: "flex-start" }}>
                      + Save current as preset
                    </button>
                  )}
                </div>
              </div>

              {/* Target resolution */}
              {!isPipeline && (
                <div className="form-row">
                  <div className="lbl-col">
                    <h4>Target resolution</h4>
                    <p>Center-crop &amp; resize before inference.</p>
                  </div>
                  <ResolutionPicker
                    targetWidth={targetWidth}
                    targetHeight={targetHeight}
                    onChange={(w, h) => { setTargetWidth(w); setTargetHeight(h); }}
                  />
                </div>
              )}

              {/* Scope */}
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Scope</h4>
                  <p>Which images to caption.</p>
                </div>
                <div className="row-flex">
                  {([
                    { value: "uncaptioned", label: "Uncaptioned only", sub: uncaptioned },
                    { value: "selected", label: "Selected", sub: selCountForDataset },
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

              {/* Delimiter */}
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Existing captions</h4>
                  <p>How to handle images that already have a caption.</p>
                </div>
                <DelimiterControls
                  mode={delimiterMode}
                  delimiterParts={delimiterParts}
                  onChange={(m, parts) => { setDelimiterMode(m); setDelimiterParts(parts); }}
                />
              </div>

              {/* Subfolder */}
              {subfolders.length > 0 && (
                <div className="form-row">
                  <div className="lbl-col">
                    <h4>Subfolder</h4>
                    <p>Limit to a specific subfolder. Ignored when scope is "Selected".</p>
                  </div>
                  <select
                    className="select"
                    value={activeSubfolder ?? ""}
                    onChange={(e) => setActiveSubfolder(e.target.value || undefined)}
                  >
                    <option value="">All subfolders</option>
                    {subfolders.map((sf) => (
                      <option key={sf.path} value={sf.path}>{sf.path || "(root)"} ({sf.image_count})</option>
                    ))}
                  </select>
                </div>
              )}

              {/* Quality Filters */}
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Quality filters</h4>
                  <p>Skip images below quality criteria.</p>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <label style={{ fontSize: 12.5, color: "var(--fg-mute)", whiteSpace: "nowrap" }}>Min aesthetic score</label>
                    <input
                      type="number"
                      className="input"
                      style={{ width: 80 }}
                      placeholder="None"
                      min={0} max={10} step={0.5}
                      value={minAestheticScore}
                      onChange={(e) => setMinAestheticScore(e.target.value)}
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
                    <p>Run bounding-box detection using the same Florence-2 model.</p>
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
                          <input type="checkbox" className="checkbox" checked={detectUseCaptions} onChange={(e) => { setDetectUseCaptions(e.target.checked); setDetectPrompt(""); }} />
                          Use each image's existing caption as prompt
                        </label>
                        {!detectUseCaptions && (
                          <input className="input" placeholder="Caption to ground…" value={detectPrompt} onChange={(e) => setDetectPrompt(e.target.value)} />
                        )}
                      </>
                    )}
                    {detectJobId && detectJobProgress && (
                      <div>
                        <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                          <div style={{ height: "100%", width: `${detectJobProgress.percent ?? 0}%`, background: "var(--accent)", transition: "width .4s" }} />
                        </div>
                        <p style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 4 }}>{detectJobProgress.message || "Detecting…"}</p>
                      </div>
                    )}
                    <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12.5 }}>
                      <input type="checkbox" className="checkbox" checked={detectOverwrite} onChange={(e) => setDetectOverwrite(e.target.checked)} />
                      Overwrite existing detections
                    </label>
                    <button
                      className="btn ghost sm"
                      style={{ alignSelf: "flex-start" }}
                      onClick={() => detectMutation.mutate()}
                      disabled={detectMutation.isPending || !!detectJobId || (detectTask === "<CAPTION_TO_PHRASE_GROUNDING>" && !detectUseCaptions && !detectPrompt.trim())}
                    >
                      {detectJobId ? "Running…" : "Run Detection"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Additional pipeline steps */}
          {additionalSteps.map((step, idx) => (
            <PipelineStepCard
              key={step.id}
              stepNumber={idx + 2}
              step={step}
              localModels={localModels}
              wd14Models={wd14Models}
              providers={providers}
              ollamaModels={ollamaModels}
              onChange={(updated) => setAdditionalSteps((prev) => prev.map((s) => s.id === step.id ? { ...s, ...updated } : s))}
              onRemove={() => setAdditionalSteps((prev) => prev.filter((s) => s.id !== step.id))}
            />
          ))}

          {/* Add Step button */}
          <button
            className="btn ghost"
            style={{ alignSelf: "flex-start" }}
            onClick={() => setAdditionalSteps((prev) => [...prev, {
              id: makeStepId(),
              model: "",
              style: "detailed",
              customPrompt: "",
              wd14Threshold: 0.35,
              providerModelInput: "",
              delimiterMode: "overwrite",
              delimiterParts: [",", " "],
              usePreviousCaption: false,
            }])}
          >
            + Add Pipeline Step
          </button>
        </div>

        {/* Right: Live progress */}
        <div className="panel">
          <div className="panel-h"><h3>Live progress</h3></div>
          <div className="panel-b">
            {otherPendingJobs.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}>
                {otherPendingJobs.map((qJob) => {
                  const fallbackLabel = qJob.job_type === "caption_pipeline" ? "Pipeline" : "Caption";
                  return (
                    <div key={qJob.job_id} style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      padding: "5px 10px",
                      background: "color-mix(in srgb, var(--accent) 8%, transparent)",
                      border: "1px solid color-mix(in srgb, var(--accent) 25%, transparent)",
                      borderRadius: "var(--r)",
                      fontSize: 12,
                    }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--fg-mute)" }}>
                        <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
                          <circle cx="8" cy="8" r="6"/>
                          <path d="M8 5v3l2 2"/>
                        </svg>
                        {qJob.label || fallbackLabel} — queued
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          useJobStore.getState().updateJob(qJob.job_id, { status: "cancelled" });
                          jobsApi.cancel(qJob.job_id);
                        }}
                        style={{ background: "none", border: "none", cursor: "pointer", color: "var(--fg-dim)", padding: 0, lineHeight: 1, fontSize: 14 }}
                        title="Cancel queued job"
                      >
                        ×
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
            {(!jobProgress || jobProgress.status === "pending") ? (
              currentIsAlsoPending ? (
                <div className="empty-state" style={{ padding: "40px 20px" }}>
                  <svg width="32" height="32" viewBox="0 0 16 16" fill="none" stroke="var(--accent)" strokeWidth="1.2">
                    <circle cx="8" cy="8" r="6"/>
                    <path d="M8 5v3l2 2"/>
                  </svg>
                  <span style={{ color: "var(--fg-dim)", fontSize: 12 }}>Queued — waiting for current job to finish</span>
                </div>
              ) : (
                <div className="empty-state" style={{ padding: "40px 20px" }}>
                  <svg width="32" height="32" viewBox="0 0 16 16" fill="none" stroke="var(--fg-soft)" strokeWidth="1.2">
                    <path d="M4 3l8 5-8 5V3z"/>
                  </svg>
                  <span style={{ color: "var(--fg-dim)", fontSize: 12 }}>Run captioning to see progress here</span>
                </div>
              )
            ) : (
              <>
                {stepIndex != null && stepTotal != null && (
                  <div style={{ marginBottom: 10, padding: "6px 10px", background: "var(--surface-2)", borderRadius: "var(--r)", fontSize: 12, color: "var(--fg-mute)" }}>
                    Pipeline: Step {stepIndex}/{stepTotal}
                  </div>
                )}
                {jobProgress.label && (
                  <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 8, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={jobProgress.label}>
                    {jobProgress.label}
                  </div>
                )}
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
                  {(jobProgress.vram_used_mb ?? 0) > 0 && (
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--fg-mute)" }}>VRAM</span>
                      <span className="mono">{Math.round(jobProgress.vram_used_mb! / 1024 * 10) / 10} GB used</span>
                    </div>
                  )}
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

                {isRunning && (
                  <button className="btn danger" style={{ width: "100%" }} onClick={handleStop} disabled={cancelMutation.isPending}>
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="3" width="10" height="10" rx="1.5"/></svg>
                    {cancelMutation.isPending ? "Stopping…" : "Stop captioning"}
                  </button>
                )}

                {isDone && failedCount === 0 && (
                  <p style={{ color: "var(--good)", fontSize: 12, marginTop: 8 }}>✓ Captioning complete</p>
                )}
                {isDone && failedCount > 0 && (
                  <div style={{
                    marginTop: 10,
                    padding: "10px 12px",
                    background: "color-mix(in srgb, var(--warn) 10%, transparent)",
                    border: "1px solid color-mix(in srgb, var(--warn) 35%, transparent)",
                    borderRadius: "var(--r)",
                    fontSize: 12,
                  }}>
                    <div style={{ color: "var(--warn)", fontWeight: 600, marginBottom: 4 }}>
                      ⚠ {failedCount} image{failedCount !== 1 ? "s" : ""} failed
                    </div>
                    <div style={{ color: "var(--fg-mute)", lineHeight: 1.5 }}>
                      The API returned an error (e.g. rate limiting or temporary outage).
                      Re-run with "Uncaptioned only" scope to retry skipped images.
                    </div>
                  </div>
                )}
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

function PipelineStepCard({
  stepNumber, step, localModels, wd14Models, providers, ollamaModels, onChange, onRemove,
}: {
  stepNumber: number;
  step: StepConfig;
  localModels: ModelInfo[];
  wd14Models: Wd14ModelInfo[];
  providers: ProviderOut[];
  ollamaModels: OllamaModel[];
  onChange: (updated: Partial<StepConfig>) => void;
  onRemove: () => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const [savingPreset, setSavingPreset] = useState(false);
  const [presetName, setPresetName] = useState("");
  const { presets, save: savePreset, remove: removePreset } = usePresetsStore();

  const isStepWd14 = step.model.startsWith("wd14:");
  const isStepOAI = step.model.startsWith("openai_compat:");
  const stepModelType = modelType(step.model);
  const stepStyles = isStepWd14 ? [] : (stepModelType ? (STYLE_LABELS[stepModelType] ?? []) : []);
  const stepProvider = isStepOAI ? providers.find((p) => step.model === `openai_compat:${p.id}`) : undefined;

  function handleSavePreset() {
    if (!presetName.trim()) return;
    savePreset({ name: presetName.trim(), model: step.model, style: step.style, prompt: step.customPrompt });
    setPresetName("");
    setSavingPreset(false);
    toast.success("Preset saved");
  }

  return (
    <div className="panel">
      <div
        className="panel-h"
        style={{ background: "var(--surface-2)", cursor: "pointer" }}
        onClick={() => setExpanded((v) => !v)}
      >
        <h3>Step {stepNumber}</h3>
        <div style={{ flex: 1 }} />
        {step.model && <span className="badge solid mono" style={{ fontSize: 10.5 }}>{step.model.split(":")[0]}</span>}
        <svg
          width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.6"
          style={{ flexShrink: 0, opacity: 0.5, transform: expanded ? "rotate(180deg)" : "none", transition: "transform .15s" }}
        >
          <path d="M2 3.5l3 3 3-3"/>
        </svg>
        <button className="icon-btn" style={{ color: "var(--bad)" }} onClick={(e) => { e.stopPropagation(); onRemove(); }} title="Remove step">×</button>
      </div>
      {expanded && (
        <div style={{ padding: "4px 22px" }}>
          <div className="form-row">
            <div className="lbl-col">
              <h4>Model</h4>
            </div>
            <StepModelPicker
              selectedModel={step.model}
              setSelectedModel={(v) => onChange({ model: v, style: "detailed", customPrompt: "" })}
              providerModelInput={step.providerModelInput}
              setProviderModelInput={(v) => onChange({ providerModelInput: v })}
              localModels={localModels}
              wd14Models={wd14Models}
              providers={providers}
              ollamaModels={ollamaModels}
            />
          </div>

          {isStepWd14 && (
            <div className="form-row">
              <div className="lbl-col"><h4>Threshold</h4></div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <input type="range" min={0} max={1} step={0.05} value={step.wd14Threshold} onChange={(e) => onChange({ wd14Threshold: parseFloat(e.target.value) })} style={{ flex: 1 }} />
                <span className="mono" style={{ fontSize: 13, minWidth: 36 }}>{step.wd14Threshold.toFixed(2)}</span>
              </div>
            </div>
          )}

          {stepStyles.length > 0 && (
            <div className="form-row">
              <div className="lbl-col"><h4>Style</h4></div>
              <div className="row-flex">
                {stepStyles.map((s) => (
                  <button key={s} className={`btn sm${step.style === s ? " primary" : ""}`} onClick={() => onChange({ style: s })}>{s}</button>
                ))}
              </div>
            </div>
          )}

          {!isStepWd14 && (
            <div className="form-row">
              <div className="lbl-col">
                <h4>Presets</h4>
                <p>Saved prompt &amp; style configurations.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {presets.length === 0 ? (
                  <p style={{ fontSize: 12, color: "var(--fg-dim)", margin: 0 }}>No presets saved yet.</p>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {presets.map((p) => (
                      <div key={p.id} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <button
                          className="btn ghost sm"
                          style={{ flex: 1, justifyContent: "flex-start", textAlign: "left" }}
                          onClick={() => { onChange({ customPrompt: p.prompt, style: p.style }); toast.success(`Loaded "${p.name}"`); }}
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
                      onKeyDown={(e) => { if (e.key === "Enter") handleSavePreset(); if (e.key === "Escape") setSavingPreset(false); }}
                      autoFocus
                      style={{ flex: 1 }}
                    />
                    <button className="btn sm primary" disabled={!presetName.trim()} onClick={handleSavePreset}>OK</button>
                    <button className="btn sm ghost" onClick={() => setSavingPreset(false)}>Cancel</button>
                  </div>
                ) : (
                  <button className="btn ghost sm" onClick={() => { setPresetName(""); setSavingPreset(true); }} style={{ alignSelf: "flex-start" }}>
                    + Save current as preset
                  </button>
                )}
              </div>
            </div>
          )}

          {!isStepWd14 && (
            <div className="form-row">
              <div className="lbl-col">
                <h4>Prompt</h4>
                <p>Use <span className="mono" style={{ fontSize: 11 }}>{"{previous_caption}"}</span> to reference the previous step's output.</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={step.usePreviousCaption}
                    onChange={(e) => onChange({ usePreviousCaption: e.target.checked })}
                  />
                  Include previous caption
                </label>
                <textarea
                  className="input"
                  style={{ height: 80 }}
                  value={step.customPrompt}
                  onChange={(e) => onChange({ customPrompt: e.target.value })}
                  placeholder="Optional additional instructions…"
                />
              </div>
            </div>
          )}

          <div className="form-row">
            <div className="lbl-col">
              <h4>Existing captions</h4>
              <p>How to handle images that already have a caption.</p>
            </div>
            <DelimiterControls
              mode={step.delimiterMode}
              delimiterParts={step.delimiterParts}
              onChange={(m, parts) => onChange({ delimiterMode: m, delimiterParts: parts })}
            />
          </div>

          {stepProvider?.is_remote && (
            <p style={{ fontSize: 11.5, color: "var(--warn)", margin: "0 0 8px" }}>
              Remote API — images will be sent to {stepProvider.base_url}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
