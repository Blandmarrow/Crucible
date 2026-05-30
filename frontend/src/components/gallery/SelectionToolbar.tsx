import { useState, useEffect, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, X, Sparkles, Star, FolderInput, ArrowRightFromLine, ScanSearch, Pencil, Maximize2, Palette, Copy } from "lucide-react";
import toast from "react-hot-toast";
import BulkEditForm from "../caption/BulkEditForm";
import UpscaleForm from "../upscale/UpscaleForm";
import LutForm from "../lut/LutForm";
import { useSelectionStore } from "../../store/selectionStore";
import { useJobStore } from "../../store/jobStore";
import { imagesApi } from "../../api/images";
import { datasetsApi } from "../../api/datasets";
import { captioningApi } from "../../api/captioning";
import { qualityApi } from "../../api/quality";
import { detectionApi } from "../../api/detection";
import ConfirmDialog from "../common/ConfirmDialog";
import MoveToDatasetModal from "../common/MoveToDatasetModal";
import PromptPresetManager from "../caption/PromptPresetManager";
import ResolutionPicker from "../caption/ResolutionPicker";
import type { ModelInfo, OllamaModel, SubfolderInfo } from "../../types";
import { type ProviderOut } from "../../api/providers";
import ModelPicker from "../providers/ModelPicker";
import { STYLE_LABELS, modelType } from "../../constants/captionStyles";
import { SUBFOLDER_RENAME_KEY } from "../../constants/storage";

interface Wd14ModelInfo { id: string; name: string; ram_mb: number; }

function resolveModelId(base: string, providerModel: string): string {
  if (base.startsWith("openai_compat:") && providerModel) return `${base}:${providerModel}`;
  return base;
}

interface Props {
  datasetId: string;
  subfolders?: SubfolderInfo[];
}

export default function SelectionToolbar({ datasetId, subfolders = [] }: Props) {
  const { selectedIds, clear, count } = useSelectionStore();
  const datasetByImageId = useSelectionStore((s) => s.datasetByImageId);
  const qc = useQueryClient();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showCaption, setShowCaption] = useState(false);
  const [showScore, setShowScore] = useState(false);
  const [captionModel, setCaptionModel] = useState("");
  const [captionStyle, setCaptionStyle] = useState("detailed");
  const [captionOverwrite, setCaptionOverwrite] = useState(false);
  const [captionCustomPrompt, setCaptionCustomPrompt] = useState("");
  const [captionProviderModel, setCaptionProviderModel] = useState("");
  const [captionWd14Threshold, setCaptionWd14Threshold] = useState(0.35);
  const [captionTargetWidth, setCaptionTargetWidth] = useState<number | null>(null);
  const [captionTargetHeight, setCaptionTargetHeight] = useState<number | null>(null);
  const [runAesthetic, setRunAesthetic] = useState(true);
  const [runTechnical, setRunTechnical] = useState(true);
  const [runWatermark, setRunWatermark] = useState(false);
  const [runEmbeddings, setRunEmbeddings] = useState(false);
  const [scoreJobLabel, setScoreJobLabel] = useState("");
  const [scoreJobId, setScoreJobId] = useState<string | null>(null);
  const [captionJobId, setCaptionJobId] = useState<string | null>(null);
  const [showMoveSubfolder, setShowMoveSubfolder] = useState(false);
  const [moveSubfolderTarget, setMoveSubfolderTarget] = useState("");
  const [showMoveDataset, setShowMoveDataset] = useState(false);
  const [showCopyDataset, setShowCopyDataset] = useState(false);
  const [showDetect, setShowDetect] = useState(false);
  const [detectModel, setDetectModel] = useState("florence2_large");
  const [detectTask, setDetectTask] = useState("<OD>");
  const [detectPrompt, setDetectPrompt] = useState("");
  const [detectUseCaptions, setDetectUseCaptions] = useState(false);
  const [detectOverwrite, setDetectOverwrite] = useState(true);
  const [detectJobLabel, setDetectJobLabel] = useState("");
  const [detectJobId, setDetectJobId] = useState<string | null>(null);
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [showUpscale, setShowUpscale] = useState(false);
  const [showLut, setShowLut] = useState(false);

  const scoreJobProgress = useJobStore((s) => s.activeJobs.get(scoreJobId ?? ""));
  const captionJobProgress = useJobStore((s) => s.activeJobs.get(captionJobId ?? ""));
  const detectJobProgress = useJobStore((s) => s.activeJobs.get(detectJobId ?? ""));

  useEffect(() => {
    if (!scoreJobId || !scoreJobProgress) return;
    if (scoreJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setScoreJobId(null);
    } else if (scoreJobProgress.status === "failed") {
      setScoreJobId(null);
      toast.error("Scoring failed");
    }
  }, [scoreJobProgress?.status, scoreJobId, datasetId, qc]);

  useEffect(() => {
    if (!captionJobId || !captionJobProgress) return;
    if (captionJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setCaptionJobId(null);
    } else if (captionJobProgress.status === "failed") {
      setCaptionJobId(null);
      toast.error("Captioning failed");
    }
  }, [captionJobProgress?.status, captionJobId, datasetId, qc]);

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

  const ids = [...selectedIds];

  const { data: modelsData } = useQuery({
    queryKey: ["captioning-models"],
    queryFn: captioningApi.models,
    enabled: showCaption,
  });

  const { data: allDatasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: datasetsApi.list,
    staleTime: 30_000,
  });

  const datasetGroups = useMemo(() => {
    const counts = new Map<string, number>();
    for (const id of selectedIds) {
      const dsId = datasetByImageId.get(id);
      if (dsId) counts.set(dsId, (counts.get(dsId) ?? 0) + 1);
    }
    return Array.from(counts.entries()).map(([id, cnt]) => ({
      id,
      name: allDatasets?.find((d) => d.id === id)?.name ?? id,
      count: cnt,
      isCurrent: id === datasetId,
    }));
  }, [selectedIds, datasetByImageId, allDatasets, datasetId]);

  const datasetBreakdown = datasetGroups.length > 0 ? (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {datasetGroups.map(({ id, name, count: cnt, isCurrent }) => (
        <span key={id} className={`badge ${isCurrent ? "badge-solid" : "badge-warn"}`}>
          {name} ×{cnt}
        </span>
      ))}
    </div>
  ) : null;

  const localModels = (modelsData?.local_models ?? []) as ModelInfo[];
  const ollamaModels = (modelsData?.ollama_models ?? []) as OllamaModel[];
  const wd14Models = (modelsData?.wd14_models ?? []) as Wd14ModelInfo[];
  const providers = (modelsData?.openai_compat_models ?? []) as ProviderOut[];
  const type = modelType(captionModel);
  const availableStyles = type ? (STYLE_LABELS[type] ?? []) : [];

  const deleteMutation = useMutation({
    mutationFn: () => imagesApi.batchDelete(ids),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      clear();
      setShowDeleteConfirm(false);
      toast.success(`Deleted ${ids.length} images`);
    },
  });

  const moveSubfolderMutation = useMutation({
    mutationFn: () => imagesApi.batchMoveSubfolder(
      ids,
      moveSubfolderTarget,
      localStorage.getItem(SUBFOLDER_RENAME_KEY) !== "off",
    ),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      setShowMoveSubfolder(false);
      clear();
      toast.success(`Moved ${data.moved} image${data.moved !== 1 ? "s" : ""} to "${data.subfolder || "(root)"}"`);
    },
    onError: () => toast.error("Move failed"),
  });

  const moveDatasetMutation = useMutation({
    mutationFn: (params: { targetId: string; subfolder: string }) =>
      imagesApi.batchMoveDataset({ image_ids: ids }, params.targetId, params.subfolder),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["subfolders", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setShowMoveDataset(false);
      clear();
      toast.success(`Moved ${data.moved} image${data.moved !== 1 ? "s" : ""} to dataset`);
    },
    onError: () => toast.error("Move to dataset failed"),
  });

  const copyDatasetMutation = useMutation({
    mutationFn: (params: { targetId: string; subfolder: string }) =>
      imagesApi.batchCopyDataset({ image_ids: ids }, params.targetId, params.subfolder),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["subfolders", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setShowCopyDataset(false);
      toast.success(`Copied ${data.copied} image${data.copied !== 1 ? "s" : ""} to dataset`);
    },
    onError: () => toast.error("Copy to dataset failed"),
  });

  const captionMutation = useMutation({
    mutationFn: () =>
      captioningApi.run({
        dataset_id: datasetId,
        image_ids: ids,
        model: resolveModelId(captionModel, captionProviderModel),
        style: captionStyle,
        overwrite: captionOverwrite,
        custom_prompt: captionCustomPrompt,
        ...(captionTargetWidth && captionTargetHeight ? { target_width: captionTargetWidth, target_height: captionTargetHeight } : {}),
        ...(captionModel.startsWith("wd14:") ? { wd14_threshold: captionWd14Threshold } : {}),
      }),
    onSuccess: (data) => {
      setShowCaption(false);
      if (data.total > 0) {
        if (data.job_id) setCaptionJobId(data.job_id);
        toast.success(`Captioning ${data.total} image${data.total !== 1 ? "s" : ""}…`);
      } else {
        toast("All images already captioned — enable overwrite to re-caption");
      }
    },
    onError: () => toast.error("Failed to start captioning"),
  });

  const scoreMutation = useMutation({
    mutationFn: () =>
      qualityApi.score({
        dataset_id: datasetId,
        image_ids: ids,
        run_aesthetic: runAesthetic,
        run_technical: runTechnical,
        run_watermark: runWatermark,
        run_embeddings: runEmbeddings,
        label: scoreJobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      setShowScore(false);
      if (data.job_id) setScoreJobId(data.job_id);
      toast.success(`Scoring ${count} image${count !== 1 ? "s" : ""}…`);
    },
    onError: () => toast.error("Failed to start scoring"),
  });

  const detectMutation = useMutation({
    mutationFn: () =>
      detectionApi.run({
        dataset_id: datasetId,
        image_ids: ids,
        model: detectModel,
        task: detectTask,
        custom_prompt: detectUseCaptions ? "" : detectPrompt,
        use_caption_as_prompt: detectUseCaptions,
        overwrite: detectOverwrite,
        label: detectJobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      setShowDetect(false);
      if (data.job_id) {
        setDetectJobId(data.job_id);
        toast.success(`Detecting objects in ${count} image${count !== 1 ? "s" : ""}…`);
      } else {
        toast("No images to process");
      }
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg ?? "Failed to start detection");
    },
  });

  const anyModalOpen = showCaption || showScore || showDetect || showBulkEdit || showUpscale || showLut || showMoveSubfolder || showDeleteConfirm;

  useEffect(() => {
    if (count === 0) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Delete") return;
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable) return;
      if (anyModalOpen) return;
      e.preventDefault();
      setShowDeleteConfirm(true);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [count, anyModalOpen]);

  if (count === 0) return null;

  return (
    <>
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 card flex items-center gap-3 px-4 py-3 shadow-xl">
        <span className="text-sm font-medium text-accent">{count} selected</span>
        {datasetGroups.length > 0 && (
          <div className="flex items-center gap-1">
            {datasetGroups.map(({ id, name, count: cnt, isCurrent }) => (
              <span
                key={id}
                className={`badge ${isCurrent ? "badge-solid" : "badge-warn"}`}
                title={isCurrent ? name : `From a different dataset: ${name}`}
              >
                {name}{datasetGroups.length > 1 || !isCurrent ? ` ×${cnt}` : ""}
              </span>
            ))}
          </div>
        )}
        <div className="w-px h-4 bg-gray-600" />

        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => setShowBulkEdit(true)}>
          <Pencil size={14} /> Edit
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => setShowUpscale(true)}>
          <Maximize2 size={14} /> Upscale
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => setShowLut(true)}>
          <Palette size={14} /> LUT
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => setShowCaption(true)}>
          <Sparkles size={14} /> Caption
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => setShowScore(true)}>
          <Star size={14} /> Score
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => setShowDetect(true)}>
          <ScanSearch size={14} /> Detect
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => { setShowMoveSubfolder(true); setMoveSubfolderTarget(""); }}>
          <FolderInput size={14} /> Move to
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => setShowMoveDataset(true)}>
          <ArrowRightFromLine size={14} /> Move to Dataset
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => setShowCopyDataset(true)}>
          <Copy size={14} /> Copy to Dataset
        </button>
        <button className="btn-danger btn-sm flex items-center gap-1.5" onClick={() => setShowDeleteConfirm(true)}>
          <Trash2 size={14} /> Delete
        </button>
        <button className="btn-ghost btn-sm p-1" onClick={clear} title="Clear selection">
          <X size={14} />
        </button>
      </div>

      {/* Caption modal */}
      {showCaption && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-md space-y-4 max-h-[80vh] overflow-y-auto">
            <h4 className="font-medium flex items-center gap-2">
              <Sparkles size={16} /> Caption {count} Image{count !== 1 ? "s" : ""}
            </h4>
            {datasetBreakdown}

            <div className="space-y-2">
              <label className="label">Model</label>
              {!modelsData && (
                <p className="text-sm text-gray-500">Loading models…</p>
              )}
              {localModels.map(m => (
                <div
                  key={m.id}
                  className={`flex items-center gap-3 p-2.5 rounded border cursor-pointer transition-colors text-sm ${
                    captionModel === m.id ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                  }`}
                  onClick={() => { setCaptionModel(m.id); setCaptionStyle("detailed"); setCaptionProviderModel(""); }}
                >
                  <div className="flex-1">{m.name}</div>
                  <span className="text-xs text-gray-500">{m.vram_mb / 1024}GB</span>
                  {m.loaded && <span className="badge-green">Loaded</span>}
                </div>
              ))}
              {ollamaModels.length > 0 && (
                <>
                  <p className="text-xs text-gray-500 pt-1">Ollama</p>
                  {ollamaModels.map(m => (
                    <div
                      key={m.id}
                      className={`flex items-center gap-3 p-2.5 rounded border cursor-pointer transition-colors text-sm ${
                        captionModel === m.id ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                      }`}
                      onClick={() => { setCaptionModel(m.id); setCaptionStyle("detailed"); setCaptionProviderModel(""); }}
                    >
                      <div className="flex-1">{m.name}</div>
                      {m.size_mb > 0 && <span className="text-xs text-gray-500">{(m.size_mb / 1024).toFixed(1)}GB</span>}
                    </div>
                  ))}
                </>
              )}
              {wd14Models.length > 0 && (
                <>
                  <p className="text-xs text-gray-500 pt-1">Tagger</p>
                  {wd14Models.map(m => (
                    <div
                      key={m.id}
                      className={`flex items-center gap-3 p-2.5 rounded border cursor-pointer transition-colors text-sm ${
                        captionModel === m.id ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                      }`}
                      onClick={() => { setCaptionModel(m.id); setCaptionStyle("detailed"); setCaptionProviderModel(""); }}
                    >
                      <div className="flex-1">{m.name}</div>
                      {m.ram_mb > 0 && <span className="text-xs text-gray-500">{(m.ram_mb / 1024).toFixed(1)} GB</span>}
                    </div>
                  ))}
                </>
              )}
              {providers.filter(p => !p.is_remote).length > 0 && (
                <>
                  <p className="text-xs text-gray-500 pt-1">Local Providers</p>
                  {providers.filter(p => !p.is_remote).map(p => {
                    const baseId = `openai_compat:${p.id}`;
                    const isSelected = captionModel.startsWith(baseId);
                    return (
                      <div key={p.id}>
                        <div
                          className={`flex items-center gap-3 p-2.5 rounded border cursor-pointer transition-colors text-sm ${
                            isSelected ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                          }`}
                          onClick={() => { setCaptionModel(baseId); setCaptionProviderModel(p.default_model); }}
                        >
                          <div className="flex-1">{p.name}</div>
                          <span className="text-xs text-gray-500 truncate max-w-[120px]">{p.base_url}</span>
                        </div>
                        {isSelected && (
                          <div className="mt-1 ml-2" onClick={e => e.stopPropagation()}>
                            <ModelPicker
                              value={captionProviderModel}
                              onChange={setCaptionProviderModel}
                              providerId={p.id}
                              baseUrl={p.base_url}
                              placeholder={`Model (default: ${p.default_model || "none"})`}
                            />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </>
              )}
              {providers.filter(p => p.is_remote).length > 0 && (
                <>
                  <p className="text-xs text-gray-500 pt-1">Cloud Providers</p>
                  {providers.filter(p => p.is_remote).map(p => {
                    const baseId = `openai_compat:${p.id}`;
                    const isSelected = captionModel.startsWith(baseId);
                    return (
                      <div key={p.id}>
                        <div
                          className={`flex items-center gap-3 p-2.5 rounded border cursor-pointer transition-colors text-sm ${
                            isSelected ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                          }`}
                          onClick={() => { setCaptionModel(baseId); setCaptionProviderModel(p.default_model); }}
                        >
                          <div className="flex-1">{p.name}</div>
                          <span className="text-xs text-gray-500 truncate max-w-[120px]">{p.base_url}</span>
                        </div>
                        {isSelected && (
                          <div className="mt-1 ml-2" onClick={e => e.stopPropagation()}>
                            <ModelPicker
                              value={captionProviderModel}
                              onChange={setCaptionProviderModel}
                              providerId={p.id}
                              baseUrl={p.base_url}
                              placeholder={`Model (default: ${p.default_model || "none"})`}
                            />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </>
              )}
            </div>

            {captionModel && (
              <>
                {captionModel.startsWith("wd14:") ? (
                  <div>
                    <label className="label">Tag Threshold</label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range" min={0} max={1} step={0.05}
                        value={captionWd14Threshold}
                        onChange={e => setCaptionWd14Threshold(Number(e.target.value))}
                        className="flex-1"
                      />
                      <span className="text-xs text-gray-400 w-8 text-right">{captionWd14Threshold.toFixed(2)}</span>
                    </div>
                  </div>
                ) : (
                  <>
                    {availableStyles.length > 0 && (
                      <div>
                        <label className="label">Style</label>
                        <div className="flex flex-wrap gap-2">
                          {availableStyles.map(s => (
                            <button
                              key={s}
                              className={`btn btn-sm ${captionStyle === s ? "btn-primary" : "btn-secondary"}`}
                              onClick={() => setCaptionStyle(s)}
                            >
                              {s}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    <div>
                      <label className="label">Custom Prompt (optional)</label>
                      <textarea
                        className="input h-16 resize-none"
                        value={captionCustomPrompt}
                        onChange={e => setCaptionCustomPrompt(e.target.value)}
                        placeholder={
                          captionModel.startsWith("ollama:") || captionModel.startsWith("openai_compat:")
                            ? "Leave blank for style preset…"
                            : "Override the default prompt for this style…"
                        }
                      />
                    </div>

                    <PromptPresetManager
                      currentModel={captionModel}
                      currentStyle={captionStyle}
                      currentPrompt={captionCustomPrompt}
                      onLoad={(p) => {
                        setCaptionModel(p.model);
                        setCaptionStyle(p.style);
                        setCaptionCustomPrompt(p.prompt);
                      }}
                    />

                    <ResolutionPicker
                      targetWidth={captionTargetWidth}
                      targetHeight={captionTargetHeight}
                      onChange={(w, h) => { setCaptionTargetWidth(w); setCaptionTargetHeight(h); }}
                    />
                  </>
                )}

                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <input type="checkbox" checked={captionOverwrite} onChange={e => setCaptionOverwrite(e.target.checked)} />
                  Overwrite existing captions
                </label>
              </>
            )}

            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setShowCaption(false)}>Cancel</button>
              <button
                className="btn-primary flex items-center gap-2"
                onClick={() => captionMutation.mutate()}
                disabled={!captionModel || captionMutation.isPending}
              >
                <Sparkles size={14} /> Start Captioning
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Score modal */}
      {showScore && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-sm space-y-4">
            <h4 className="font-medium flex items-center gap-2">
              <Star size={16} /> Score {count} Image{count !== 1 ? "s" : ""}
            </h4>
            {datasetBreakdown}
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer text-sm">
                <input type="checkbox" checked={runAesthetic} onChange={e => setRunAesthetic(e.target.checked)} />
                Aesthetic Score (LAION predictor)
              </label>
              <label className="flex items-center gap-2 cursor-pointer text-sm">
                <input type="checkbox" checked={runTechnical} onChange={e => setRunTechnical(e.target.checked)} />
                Technical (blur, noise, duplicates)
              </label>
              <label className="flex items-center gap-2 cursor-pointer text-sm">
                <input type="checkbox" checked={runWatermark} onChange={e => setRunWatermark(e.target.checked)} />
                Watermark detection (CLIP zero-shot)
              </label>
              <label className="flex items-center gap-2 cursor-pointer text-sm">
                <input type="checkbox" checked={runEmbeddings} onChange={e => setRunEmbeddings(e.target.checked)} />
                Style embeddings (CLIP + DINOv2, for similarity)
              </label>
            </div>
            <input
              className="input w-full"
              type="text"
              placeholder="Job label (optional)"
              value={scoreJobLabel}
              onChange={(e) => setScoreJobLabel(e.target.value)}
              style={{ fontSize: 12 }}
              title="Optional name shown in the job queue"
            />
            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setShowScore(false)}>Cancel</button>
              <button
                className="btn-primary flex items-center gap-2"
                onClick={() => scoreMutation.mutate()}
                disabled={(!runAesthetic && !runTechnical && !runWatermark && !runEmbeddings) || scoreMutation.isPending}
              >
                <Star size={14} /> Run Scoring
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Detection modal */}
      {showDetect && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-sm space-y-4">
            <h4 className="font-medium flex items-center gap-2">
              <ScanSearch size={16} /> Detect Objects in {count} Image{count !== 1 ? "s" : ""}
            </h4>
            {datasetBreakdown}

            <div>
              <label className="label">Model</label>
              <select
                className="select w-full"
                value={detectModel}
                onChange={(e) => setDetectModel(e.target.value)}
              >
                <option value="florence2_large">Florence-2 Large</option>
                <option value="florence2_promptgen">Florence-2 PromptGen</option>
              </select>
            </div>

            <div>
              <label className="label">Task</label>
              <select
                className="select w-full"
                value={detectTask}
                onChange={(e) => { setDetectTask(e.target.value); setDetectPrompt(""); setDetectUseCaptions(false); }}
              >
                <option value="<OD>">Object Detection (auto-detect everything)</option>
                <option value="<CAPTION_TO_PHRASE_GROUNDING>">Grounded Caption (draw boxes around phrases)</option>
              </select>
            </div>

            {detectTask === "<CAPTION_TO_PHRASE_GROUNDING>" && (
              <div className="space-y-2">
                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={detectUseCaptions}
                    onChange={(e) => { setDetectUseCaptions(e.target.checked); setDetectPrompt(""); }}
                  />
                  Use each image's existing caption as prompt
                </label>
                {!detectUseCaptions && (
                  <>
                    <label className="label">Caption to ground</label>
                    <input
                      className="input"
                      placeholder="e.g. a cat sitting on a dog"
                      value={detectPrompt}
                      onChange={(e) => setDetectPrompt(e.target.value)}
                      autoFocus
                    />
                    <p className="text-xs text-gray-500">
                      Florence-2 will draw boxes around the phrases from this caption.
                    </p>
                  </>
                )}
                {detectUseCaptions && (
                  <p className="text-xs text-gray-500">
                    Images without a caption will be skipped.
                  </p>
                )}
              </div>
            )}

            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input type="checkbox" checked={detectOverwrite} onChange={e => setDetectOverwrite(e.target.checked)} />
              Overwrite existing detections
            </label>

            <input
              className="input w-full"
              type="text"
              placeholder="Job label (optional)"
              value={detectJobLabel}
              onChange={(e) => setDetectJobLabel(e.target.value)}
              style={{ fontSize: 12 }}
              title="Optional name shown in the job queue"
            />

            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setShowDetect(false)}>Cancel</button>
              <button
                className="btn-primary flex items-center gap-2"
                onClick={() => detectMutation.mutate()}
                disabled={
                  detectMutation.isPending ||
                  (detectTask === "<CAPTION_TO_PHRASE_GROUNDING>" && !detectUseCaptions && !detectPrompt.trim())
                }
              >
                <ScanSearch size={14} /> Run Detection
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk edit caption modal */}
      {showBulkEdit && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-md space-y-1 max-h-[80vh] overflow-y-auto">
            <h4 className="font-medium flex items-center gap-2 mb-1">
              <Pencil size={15} /> Edit Captions — {count} Image{count !== 1 ? "s" : ""}
            </h4>
            {datasetBreakdown}
            <BulkEditForm
              datasetId={datasetId}
              imageIds={ids}
              onSuccess={() => { setShowBulkEdit(false); clear(); }}
              onCancel={() => setShowBulkEdit(false)}
            />
          </div>
        </div>
      )}

      {/* Upscale modal */}
      {showUpscale && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-md space-y-1 max-h-[80vh] overflow-y-auto">
            <h4 className="font-medium flex items-center gap-2 mb-1">
              <Maximize2 size={15} /> Upscale {count} Image{count !== 1 ? "s" : ""}
            </h4>
            {datasetBreakdown}
            <UpscaleForm
              datasetId={datasetId}
              imageIds={ids}
              onSuccess={() => setShowUpscale(false)}
              onCancel={() => setShowUpscale(false)}
            />
          </div>
        </div>
      )}

      {/* LUT modal */}
      {showLut && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-md space-y-1 max-h-[80vh] overflow-y-auto">
            <h4 className="font-medium flex items-center gap-2 mb-1">
              <Palette size={15} /> Apply LUT — {count} Image{count !== 1 ? "s" : ""}
            </h4>
            {datasetBreakdown}
            <LutForm
              datasetId={datasetId}
              imageIds={ids}
              onSuccess={() => setShowLut(false)}
              onCancel={() => setShowLut(false)}
            />
          </div>
        </div>
      )}

      {showDeleteConfirm && (
        <ConfirmDialog
          title={`Delete ${count} Images`}
          message="This will permanently delete the selected images and their captions."
          confirmLabel="Delete All"
          danger
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}

      {/* Move to dataset modal */}
      {showMoveDataset && (
        <MoveToDatasetModal
          count={count}
          currentDatasetId={datasetId}
          isPending={moveDatasetMutation.isPending}
          onConfirm={(targetId, subfolder) => moveDatasetMutation.mutate({ targetId, subfolder })}
          onClose={() => setShowMoveDataset(false)}
          sourceInfo={datasetBreakdown}
        />
      )}

      {/* Copy to dataset modal */}
      {showCopyDataset && (
        <MoveToDatasetModal
          mode="copy"
          count={count}
          currentDatasetId={datasetId}
          isPending={copyDatasetMutation.isPending}
          onConfirm={(targetId, subfolder) => copyDatasetMutation.mutate({ targetId, subfolder })}
          onClose={() => setShowCopyDataset(false)}
          sourceInfo={datasetBreakdown}
        />
      )}

      {/* Move to subfolder modal */}
      {showMoveSubfolder && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-sm space-y-3">
            <h4 className="font-medium flex items-center gap-2">
              <FolderInput size={15} /> Move {count} Image{count !== 1 ? "s" : ""} to Subfolder
            </h4>
            {datasetBreakdown}
            {subfolders.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                <button
                  className={`btn btn-sm ${moveSubfolderTarget === "" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setMoveSubfolderTarget("")}
                >
                  (root)
                </button>
                {subfolders.filter(sf => sf.path !== "").map(sf => (
                  <button
                    key={sf.path}
                    className={`btn btn-sm ${moveSubfolderTarget === sf.path ? "btn-primary" : "btn-secondary"}`}
                    onClick={() => setMoveSubfolderTarget(sf.path)}
                  >
                    {sf.path}
                  </button>
                ))}
              </div>
            )}
            <input
              className="input"
              placeholder={subfolders.length > 0 ? "Or type a new path: characters/poses" : "Subfolder path: characters/poses"}
              value={moveSubfolderTarget}
              onChange={e => setMoveSubfolderTarget(e.target.value)}
              autoFocus={subfolders.length === 0}
            />
            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setShowMoveSubfolder(false)}>Cancel</button>
              <button
                className="btn-primary"
                onClick={() => moveSubfolderMutation.mutate()}
                disabled={moveSubfolderMutation.isPending}
              >
                Move
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
