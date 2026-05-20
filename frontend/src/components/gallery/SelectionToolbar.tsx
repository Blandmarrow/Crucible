import { useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, Tag, Tags, X, Sparkles, Star, FolderInput, ScanSearch } from "lucide-react";
import toast from "react-hot-toast";
import { useSelectionStore } from "../../store/selectionStore";
import { useJobStore } from "../../store/jobStore";
import { imagesApi } from "../../api/images";
import { captionsApi } from "../../api/captions";
import { captioningApi } from "../../api/captioning";
import { qualityApi } from "../../api/quality";
import { detectionApi } from "../../api/detection";
import ConfirmDialog from "../common/ConfirmDialog";
import PromptPresetManager from "../caption/PromptPresetManager";
import ResolutionPicker from "../caption/ResolutionPicker";
import type { ModelInfo, OllamaModel, SubfolderInfo } from "../../types";
import { STYLE_LABELS, modelType } from "../../constants/captionStyles";

interface Props {
  datasetId: string;
  subfolders?: SubfolderInfo[];
}

export default function SelectionToolbar({ datasetId, subfolders = [] }: Props) {
  const { selectedIds, clear, count } = useSelectionStore();
  const qc = useQueryClient();
  const [showTagAdd, setShowTagAdd] = useState(false);
  const [showTagRemove, setShowTagRemove] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showCaption, setShowCaption] = useState(false);
  const [showScore, setShowScore] = useState(false);
  const [captionModel, setCaptionModel] = useState("");
  const [captionStyle, setCaptionStyle] = useState("detailed");
  const [captionOverwrite, setCaptionOverwrite] = useState(false);
  const [captionCustomPrompt, setCaptionCustomPrompt] = useState("");
  const [captionTargetWidth, setCaptionTargetWidth] = useState<number | null>(null);
  const [captionTargetHeight, setCaptionTargetHeight] = useState<number | null>(null);
  const [runAesthetic, setRunAesthetic] = useState(true);
  const [runTechnical, setRunTechnical] = useState(true);
  const [runWatermark, setRunWatermark] = useState(false);
  const [runEmbeddings, setRunEmbeddings] = useState(false);
  const [scoreJobId, setScoreJobId] = useState<string | null>(null);
  const [captionJobId, setCaptionJobId] = useState<string | null>(null);
  const [showMoveSubfolder, setShowMoveSubfolder] = useState(false);
  const [moveSubfolderTarget, setMoveSubfolderTarget] = useState("");
  const [showDetect, setShowDetect] = useState(false);
  const [detectModel, setDetectModel] = useState("florence2_large");
  const [detectTask, setDetectTask] = useState("<OD>");
  const [detectPrompt, setDetectPrompt] = useState("");
  const [detectUseCaptions, setDetectUseCaptions] = useState(false);
  const [detectOverwrite, setDetectOverwrite] = useState(true);
  const [detectJobId, setDetectJobId] = useState<string | null>(null);

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

  const localModels = (modelsData?.local_models ?? []) as ModelInfo[];
  const ollamaModels = (modelsData?.ollama_models ?? []) as OllamaModel[];
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
    mutationFn: () => imagesApi.batchMoveSubfolder(ids, moveSubfolderTarget),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      setShowMoveSubfolder(false);
      clear();
      toast.success(`Moved ${data.moved} image${data.moved !== 1 ? "s" : ""} to "${data.subfolder || "(root)"}"`);
    },
    onError: () => toast.error("Move failed"),
  });

  const addTagsMutation = useMutation({
    mutationFn: () => captionsApi.batchSetTags(ids, tagInput.split(",").map(t => t.trim()).filter(Boolean)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setShowTagAdd(false);
      setTagInput("");
      toast.success("Tags added");
    },
  });

  const removeTagsMutation = useMutation({
    mutationFn: () => captionsApi.batchRemoveTags(ids, tagInput.split(",").map(t => t.trim()).filter(Boolean)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setShowTagRemove(false);
      setTagInput("");
      toast.success("Tags removed");
    },
  });

  const captionMutation = useMutation({
    mutationFn: () =>
      captioningApi.run({
        dataset_id: datasetId,
        image_ids: ids,
        model: captionModel,
        style: captionStyle,
        overwrite: captionOverwrite,
        custom_prompt: captionCustomPrompt,
        ...(captionTargetWidth && captionTargetHeight ? { target_width: captionTargetWidth, target_height: captionTargetHeight } : {}),
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

  if (count === 0) return null;

  return (
    <>
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 card flex items-center gap-3 px-4 py-3 shadow-xl">
        <span className="text-sm font-medium text-accent">{count} selected</span>
        <div className="w-px h-4 bg-gray-600" />

        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => { setShowTagAdd(true); setTagInput(""); }}>
          <Tag size={14} /> Add Tags
        </button>
        <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => { setShowTagRemove(true); setTagInput(""); }}>
          <Tags size={14} /> Remove Tags
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
        <button className="btn-danger btn-sm flex items-center gap-1.5" onClick={() => setShowDeleteConfirm(true)}>
          <Trash2 size={14} /> Delete
        </button>
        <button className="btn-ghost btn-sm p-1" onClick={clear} title="Clear selection">
          <X size={14} />
        </button>
      </div>

      {/* Tag add modal */}
      {showTagAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-sm space-y-3">
            <h4 className="font-medium">Add Tags to {count} Images</h4>
            <input className="input" placeholder="tag1, tag2, tag3" value={tagInput} onChange={e => setTagInput(e.target.value)} autoFocus />
            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setShowTagAdd(false)}>Cancel</button>
              <button className="btn-primary" onClick={() => addTagsMutation.mutate()} disabled={!tagInput}>Apply</button>
            </div>
          </div>
        </div>
      )}

      {/* Tag remove modal */}
      {showTagRemove && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-sm space-y-3">
            <h4 className="font-medium">Remove Tags from {count} Images</h4>
            <input className="input" placeholder="tag1, tag2, tag3" value={tagInput} onChange={e => setTagInput(e.target.value)} autoFocus />
            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setShowTagRemove(false)}>Cancel</button>
              <button className="btn-danger" onClick={() => removeTagsMutation.mutate()} disabled={!tagInput}>Remove</button>
            </div>
          </div>
        </div>
      )}

      {/* Caption modal */}
      {showCaption && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-md space-y-4 max-h-[80vh] overflow-y-auto">
            <h4 className="font-medium flex items-center gap-2">
              <Sparkles size={16} /> Caption {count} Image{count !== 1 ? "s" : ""}
            </h4>

            <div className="space-y-2">
              <label className="label">Model</label>
              {localModels.length === 0 && ollamaModels.length === 0 && (
                <p className="text-sm text-gray-500">Loading models…</p>
              )}
              {localModels.map(m => (
                <div
                  key={m.id}
                  className={`flex items-center gap-3 p-2.5 rounded border cursor-pointer transition-colors text-sm ${
                    captionModel === m.id ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                  }`}
                  onClick={() => { setCaptionModel(m.id); setCaptionStyle("detailed"); }}
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
                      onClick={() => { setCaptionModel(m.id); setCaptionStyle("detailed"); }}
                    >
                      <div className="flex-1">{m.name}</div>
                      {m.size_mb > 0 && <span className="text-xs text-gray-500">{(m.size_mb / 1024).toFixed(1)}GB</span>}
                    </div>
                  ))}
                </>
              )}
            </div>

            {captionModel && (
              <>
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

                <div>
                  <label className="label">Custom Prompt (optional)</label>
                  <textarea
                    className="input h-16 resize-none"
                    value={captionCustomPrompt}
                    onChange={e => setCaptionCustomPrompt(e.target.value)}
                    placeholder={
                      captionModel.startsWith("ollama:")
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

      {/* Move to subfolder modal */}
      {showMoveSubfolder && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-sm space-y-3">
            <h4 className="font-medium flex items-center gap-2">
              <FolderInput size={15} /> Move {count} Image{count !== 1 ? "s" : ""} to Subfolder
            </h4>
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
