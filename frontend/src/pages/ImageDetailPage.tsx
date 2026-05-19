import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { usePaneDatasetId, usePaneImageId } from "../hooks/usePaneDatasetId";
import { usePaneNavigate } from "../hooks/usePaneNavigate";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, Save, Crop, AlertTriangle, Copy, Sparkles, ChevronDown, ChevronUp, Type } from "lucide-react";
import Cropper from "react-easy-crop";
import toast from "react-hot-toast";
import { imagesApi } from "../api/images";
import { captionsApi } from "../api/captions";
import { captioningApi } from "../api/captioning";
import { useJobStore } from "../store/jobStore";
import PromptPresetManager from "../components/caption/PromptPresetManager";
import ResolutionPicker from "../components/caption/ResolutionPicker";
import GenerationMetadata from "../components/image/GenerationMetadata";
import type { ModelInfo, OllamaModel } from "../types";
import { STYLE_LABELS, modelType } from "../constants/captionStyles";
import { DINO_LAYER_LABELS } from "../constants/dinoLabels";

function formatSize(bytes: number | null) {
  if (!bytes) return "—";
  return bytes < 1_048_576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1_048_576).toFixed(1)} MB`;
}

interface CropArea { x: number; y: number; width: number; height: number; }

function DinoLayerBreakdown({ scores }: { scores: Record<string, number> }) {
  const [open, setOpen] = useState(true);
  const layers = Array.from({ length: 12 }, (_, i) => String(i + 1))
    .filter((k) => scores[k] !== undefined);
  const maxScore = Math.max(...layers.map((k) => scores[k]));

  return (
    <div style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
      <button
        className="icon-btn"
        style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", justifyContent: "space-between", padding: "2px 0", background: "none", border: "none" }}
        onClick={() => setOpen((v) => !v)}
      >
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--fg-mute)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          DINOv2 layer breakdown
        </span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {open && (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
          {layers.map((k) => {
            const score = scores[k];
            const pct = maxScore > 0 ? (score / maxScore) * 100 : 0;
            const label = DINO_LAYER_LABELS[k] ?? `Layer ${k}`;
            return (
              <div key={k} style={{ display: "grid", gridTemplateColumns: "20px 1fr auto", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 10, color: "var(--fg)", textAlign: "right", fontFamily: "monospace" }}>{k}</span>
                <div style={{ position: "relative", height: 14, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }} title={label}>
                  <div style={{ position: "absolute", inset: "0 auto 0 0", width: `${pct}%`, background: "var(--accent)", borderRadius: 3, transition: "width .3s" }} />
                  <span style={{ position: "absolute", left: 4, top: 0, lineHeight: "14px", fontSize: 9, color: "var(--fg)", whiteSpace: "nowrap", overflow: "hidden", maxWidth: "calc(100% - 8px)" }}>
                    {label}
                  </span>
                </div>
                <span style={{ fontSize: 10, color: "var(--fg)", fontFamily: "monospace", minWidth: 32, textAlign: "right" }}>
                  {(score * 100).toFixed(0)}%
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function ImageDetailPage() {
  const datasetId = usePaneDatasetId();
  const imageId = usePaneImageId();
  const { go: paneGo, back: paneBack } = usePaneNavigate();
  const qc = useQueryClient();

  const [tags, setTags] = useState<string[]>([]);
  const [captionText, setCaptionText] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const captionRef = useRef<HTMLTextAreaElement>(null);
  const [captionDirty, setCaptionDirty] = useState(false);
  const [cropMode, setCropMode] = useState(false);
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [aspect, setAspect] = useState<number | undefined>(undefined);
  const [croppedArea, setCroppedArea] = useState<CropArea | null>(null);

  // AI captioning state
  const [showAi, setShowAi] = useState(false);
  const [aiModel, setAiModel] = useState("");
  const [aiStyle, setAiStyle] = useState("detailed");
  const [aiCustomPrompt, setAiCustomPrompt] = useState("");
  const [aiTargetWidth, setAiTargetWidth] = useState<number | null>(null);
  const [aiTargetHeight, setAiTargetHeight] = useState<number | null>(null);
  const [aiJobId, setAiJobId] = useState<string | null>(null);

  // Navigation context written by GalleryPage — re-read whenever imageId changes (we may have
  // updated sessionStorage just before navigating, so the fresh read gets the new page's data)
  const navCtx = useMemo(() => {
    if (!datasetId) return null;
    try {
      const raw = sessionStorage.getItem(`gallery-nav-${datasetId}`);
      return raw
        ? (JSON.parse(raw) as { ids: string[]; page: number; sort: string; order: string; captionedFilter: boolean | null })
        : null;
    } catch { return null; }
  }, [datasetId, imageId]); // eslint-disable-line react-hooks/exhaustive-deps

  const currentIndex = navCtx ? navCtx.ids.indexOf(imageId ?? "") : -1;
  // "at end" means last slot of a full page — there may be a next page
  const atEnd = !!navCtx && currentIndex === navCtx.ids.length - 1 && navCtx.ids.length === 100;
  const atStart = currentIndex === 0 && !!navCtx && navCtx.page > 1;

  const { data: nextPageData } = useQuery({
    queryKey: ["gallery-nav", datasetId, navCtx?.page, navCtx?.sort, navCtx?.order, navCtx?.captionedFilter, "next"],
    queryFn: () => imagesApi.list({
      dataset_id: datasetId!,
      page: navCtx!.page + 1,
      limit: 100,
      sort: navCtx!.sort,
      order: navCtx!.order,
      captioned: navCtx?.captionedFilter ?? undefined,
    }),
    enabled: atEnd,
    staleTime: 60_000,
  });

  const { data: prevPageData } = useQuery({
    queryKey: ["gallery-nav", datasetId, navCtx?.page, navCtx?.sort, navCtx?.order, navCtx?.captionedFilter, "prev"],
    queryFn: () => imagesApi.list({
      dataset_id: datasetId!,
      page: navCtx!.page - 1,
      limit: 100,
      sort: navCtx!.sort,
      order: navCtx!.order,
      captioned: navCtx?.captionedFilter ?? undefined,
    }),
    enabled: atStart,
    staleTime: 60_000,
  });

  const prevId =
    currentIndex > 0 ? navCtx!.ids[currentIndex - 1]
    : atStart && prevPageData?.length ? prevPageData[prevPageData.length - 1].id
    : null;

  const nextId =
    navCtx && currentIndex >= 0 && currentIndex < navCtx.ids.length - 1 ? navCtx.ids[currentIndex + 1]
    : atEnd && nextPageData?.length ? nextPageData[0].id
    : null;

  const goTo = useCallback((id: string) => {
    // When crossing a page boundary, update the nav context so subsequent navigation
    // continues through the new page, and sync the gallery's saved page so Back lands correctly.
    if (navCtx && datasetId) {
      let newCtx: typeof navCtx | null = null;
      if (atEnd && id === nextId && nextPageData?.length) {
        newCtx = { ...navCtx, ids: nextPageData.map((i) => i.id), page: navCtx.page + 1 };
      } else if (atStart && id === prevId && prevPageData?.length) {
        newCtx = { ...navCtx, ids: prevPageData.map((i) => i.id), page: navCtx.page - 1 };
      }
      if (newCtx) {
        sessionStorage.setItem(`gallery-nav-${datasetId}`, JSON.stringify(newCtx));
        // Also update gallery state so "Back" returns to the right page
        try {
          const raw = sessionStorage.getItem(`gallery-state-${datasetId}`);
          if (raw) {
            const state = JSON.parse(raw);
            sessionStorage.setItem(`gallery-state-${datasetId}`, JSON.stringify({ ...state, page: newCtx.page, scrollTop: 0 }));
          }
        } catch {}
      }
    }
    paneGo(`/datasets/${datasetId}/image/${id}`, { page: "image-detail", datasetId: datasetId ?? "", imageId: id }, { replace: true });
  }, [navCtx, datasetId, atEnd, atStart, nextId, prevId, nextPageData, prevPageData, paneGo]);

  // Arrow-key navigation — skip when focus is inside a text field
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || (e.target as HTMLElement)?.isContentEditable) return;
      if (e.key === "ArrowLeft" && prevId) goTo(prevId);
      if (e.key === "ArrowRight" && nextId) goTo(nextId);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [prevId, nextId, goTo]);

  const { data: image, isLoading: imageLoading } = useQuery({
    queryKey: ["image", imageId],
    queryFn: () => imagesApi.get(imageId!),
    enabled: !!imageId,
  });

  const { data: captionData } = useQuery({
    queryKey: ["caption", imageId],
    queryFn: () => captionsApi.get(imageId!),
    enabled: !!imageId,
  });

  const { data: modelsData } = useQuery({
    queryKey: ["captioning-models"],
    queryFn: captioningApi.models,
    enabled: showAi,
  });

  const localModels = (modelsData?.local_models ?? []) as ModelInfo[];
  const ollamaModels = (modelsData?.ollama_models ?? []) as OllamaModel[];
  const aiModelType = modelType(aiModel);
  const aiStyles = aiModelType ? (STYLE_LABELS[aiModelType] ?? []) : [];

  // Track AI job progress from the global SSE store (TopBar already subscribes to all jobs)
  const aiJobProgress = useJobStore((s) => s.activeJobs.get(aiJobId ?? ""));

  // When AI job completes, refresh caption
  useEffect(() => {
    if (!aiJobId || !aiJobProgress) return;
    if (aiJobProgress.status === "completed") {
      setCaptionDirty(false);
      qc.invalidateQueries({ queryKey: ["caption", imageId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setAiJobId(null);
      toast.success("Caption generated");
    } else if (aiJobProgress.status === "failed") {
      setAiJobId(null);
      toast.error("Captioning failed");
    }
  }, [aiJobProgress?.status, aiJobId, imageId, datasetId, qc]);

  useEffect(() => {
    if (captionData && !captionDirty) {
      setTags(captionData.tags);
      setCaptionText(captionData.caption_text);
      setCaptionStyle(captionData.caption_style);
    }
  }, [captionData]);

  useEffect(() => {
    const el = captionRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [captionText]);

  const saveMutation = useMutation({
    mutationFn: () => captionsApi.update(imageId!, { caption_text: captionText, tags, caption_style: captionStyle }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["caption", imageId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setCaptionDirty(false);
      toast.success("Saved");
    },
    onError: () => toast.error("Save failed"),
  });

  const cropMutation = useMutation({
    mutationFn: () => {
      if (!croppedArea) throw new Error("No crop area");
      return imagesApi.crop(imageId!, croppedArea);
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["image", imageId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setCropMode(false);
      toast.success(`Cropped to ${data.width}×${data.height}`);
    },
    onError: () => toast.error("Crop failed"),
  });

  const aiMutation = useMutation({
    mutationFn: () =>
      captioningApi.run({
        dataset_id: datasetId!,
        image_ids: [imageId!],
        model: aiModel,
        style: aiStyle,
        overwrite: true,
        custom_prompt: aiCustomPrompt,
        ...(aiTargetWidth && aiTargetHeight ? { target_width: aiTargetWidth, target_height: aiTargetHeight } : {}),
      }),
    onSuccess: (data) => {
      if (data.job_id) {
        setAiJobId(data.job_id);
      } else {
        toast("Caption already exists — generation skipped");
      }
    },
    onError: () => toast.error("Failed to start captioning"),
  });

  const onCropComplete = useCallback((_: unknown, croppedPixels: CropArea) => {
    setCroppedArea(croppedPixels);
  }, []);

  if (imageLoading || !image) {
    return <div className="p-8 text-gray-500">Loading...</div>;
  }

  const isDuplicate = image.quality_flags?.is_duplicate as boolean | undefined;
  const isBlurry = image.quality_flags?.is_blurry as boolean | undefined;
  const isUniform = image.quality_flags?.is_uniform as boolean | undefined;
  const hasWatermark = image.quality_flags?.has_watermark as boolean | undefined;
  const aiRunning = !!aiJobId && aiJobProgress?.status === "running";

  return (
    <div className="flex h-full">
      {/* Left: image */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="p-3 border-b border-gray-700/50 flex items-center gap-3">
          <button className="btn-ghost btn-sm flex items-center gap-1.5" onClick={() => paneBack({ page: "gallery", datasetId: datasetId ?? "" })}>
            <ArrowLeft size={14} /> Back
          </button>

          {navCtx && currentIndex >= 0 && (
            <div className="flex items-center gap-1">
              <button
                className="btn-ghost btn-sm p-1"
                onClick={() => prevId && goTo(prevId)}
                disabled={!prevId}
                title="Previous image (←)"
              >
                <ChevronLeft size={16} />
              </button>
              <span className="text-xs text-gray-500 tabular-nums w-20 text-center">
                {currentIndex + 1} / {navCtx.ids.length}
                {navCtx.page > 1 && <span className="ml-1 text-gray-600">p.{navCtx.page}</span>}
              </span>
              <button
                className="btn-ghost btn-sm p-1"
                onClick={() => nextId && goTo(nextId)}
                disabled={!nextId}
                title="Next image (→)"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          )}

          <span className="text-sm text-gray-400 truncate">{image.filename}</span>
          <div className="flex-1" />
          <button
            className={`btn-sm flex items-center gap-1.5 ${cropMode ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setCropMode((v) => !v)}
          >
            <Crop size={14} /> {cropMode ? "Cancel Crop" : "Crop"}
          </button>
          {cropMode && (
            <>
              <select className="input w-28" value={aspect ?? ""} onChange={(e) => setAspect(e.target.value ? Number(e.target.value) : undefined)}>
                <option value="">Free</option>
                <option value={1}>1:1</option>
                <option value={4/3}>4:3</option>
                <option value={16/9}>16:9</option>
                <option value={3/2}>3:2</option>
                <option value={9/16}>9:16</option>
              </select>
              <button className="btn-primary btn-sm" onClick={() => cropMutation.mutate()} disabled={!croppedArea}>
                Apply Crop
              </button>
            </>
          )}
        </div>

        <div className="flex-1 relative bg-black/40">
          {cropMode ? (
            <Cropper
              image={imagesApi.fileUrl(imageId!)}
              crop={crop}
              zoom={zoom}
              aspect={aspect}
              onCropChange={setCrop}
              onZoomChange={setZoom}
              onCropComplete={onCropComplete}
            />
          ) : (
            <img
              src={imagesApi.fileUrl(imageId!)}
              alt={image.filename}
              className="max-w-full max-h-full object-contain absolute inset-0 m-auto"
            />
          )}
        </div>
      </div>

      {/* Right: metadata + caption panel */}
      <div className="w-80 bg-surface-card border-l border-gray-700/50 flex flex-col overflow-y-auto">
        {/* Meta */}
        <div className="p-4 border-b border-gray-700/50 space-y-2">
          <h3 className="font-medium text-sm text-gray-300 uppercase tracking-wide">Image Info</h3>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <span className="text-gray-500">Dimensions</span>
            <span>{image.width}×{image.height}</span>
            <span className="text-gray-500">Size</span>
            <span>{formatSize(image.file_size_bytes)}</span>
            <span className="text-gray-500">Format</span>
            <span>{image.format}</span>
            {image.aesthetic_score !== null && (
              <>
                <span className="text-gray-500">Aesthetic</span>
                <span className={image.aesthetic_score >= 6 ? "text-green-400" : image.aesthetic_score >= 4 ? "text-yellow-400" : "text-red-400"}>
                  {image.aesthetic_score?.toFixed(2)}/10
                </span>
              </>
            )}
            {image.blur_score !== null && (
              <>
                <span className="text-gray-500">Blur score</span>
                <span>{image.blur_score?.toFixed(1)}</span>
              </>
            )}
            {image.uniformity_score !== null && image.uniformity_score !== undefined && (
              <>
                <span className="text-gray-500">Uniformity</span>
                <span className={isUniform ? "text-orange-400" : ""}>
                  {image.uniformity_score.toFixed(1)}{isUniform ? " (flat)" : ""}
                </span>
              </>
            )}
            {image.watermark_score !== null && image.watermark_score !== undefined && (
              <>
                <span className="text-gray-500">Watermark</span>
                <span className={hasWatermark ? "text-blue-400" : "text-gray-300"}>
                  {(image.watermark_score * 100).toFixed(0)}%
                </span>
              </>
            )}
            {image.color_score !== null && image.color_score !== undefined && (
              <>
                <span className="text-gray-500">Colorfulness</span>
                <span>{image.color_score.toFixed(1)}</span>
              </>
            )}
            {image.saturation_score !== null && image.saturation_score !== undefined && (
              <>
                <span className="text-gray-500">Saturation</span>
                <span>{(image.saturation_score * 100).toFixed(0)}%</span>
              </>
            )}
            {image.style_similarity_score !== null && image.style_similarity_score !== undefined && (
              <>
                <span className="text-gray-500">Style match</span>
                <span>{(image.style_similarity_score * 100).toFixed(0)}%</span>
              </>
            )}
          </div>

          {image.dino_layer_scores && Object.keys(image.dino_layer_scores).length > 0 ? (
            <DinoLayerBreakdown scores={image.dino_layer_scores} />
          ) : image.has_dino_layer_embeddings ? (
            <p className="text-[11px] text-fg opacity-50 mt-1">
              Per-layer embeddings stored — run style similarity with "All layers" to score them.
            </p>
          ) : null}

          {/* Quality flags */}
          {(isDuplicate === true || isBlurry === true || isUniform === true || hasWatermark === true) && (
            <div className="flex gap-2 flex-wrap mt-2">
              {isBlurry === true && <span className="badge-yellow flex items-center gap-1"><AlertTriangle size={10} />Blurry</span>}
              {isDuplicate === true && <span className="badge-yellow flex items-center gap-1"><Copy size={10} />Duplicate</span>}
              {isUniform === true && <span className="badge-orange flex items-center gap-1"><AlertTriangle size={10} />Near-uniform</span>}
              {hasWatermark === true && <span className="badge-blue flex items-center gap-1"><Type size={10} />Watermark</span>}
            </div>
          )}

          {/* AI generation metadata */}
          {image.generation_metadata && (
            <GenerationMetadata metadata={image.generation_metadata} />
          )}
        </div>

        {/* Caption */}
        <div className="p-4 flex-1 space-y-3">
          <h3 className="font-medium text-sm text-gray-300 uppercase tracking-wide">Caption</h3>

          <div>
            <label className="label">Caption Text</label>
            <textarea
              ref={captionRef}
              className="input resize-none overflow-hidden"
              style={{ minHeight: "8rem" }}
              value={captionText}
              onChange={(e) => { setCaptionText(e.target.value); setCaptionDirty(true); }}
              placeholder="Natural language description..."
            />
          </div>

          <button
            className="btn-primary w-full flex items-center justify-center gap-2"
            onClick={() => saveMutation.mutate()}
            disabled={!captionDirty || saveMutation.isPending}
          >
            <Save size={14} /> Save
          </button>

          {/* AI Generate section */}
          <div className="border-t border-gray-700/50 pt-3">
            <button
              className="flex items-center justify-between w-full text-sm font-medium text-gray-300 hover:text-white transition-colors"
              onClick={() => setShowAi((v) => !v)}
            >
              <span className="flex items-center gap-2">
                <Sparkles size={14} className="text-accent" /> Generate with AI
              </span>
              {showAi ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>

            {showAi && (
              <div className="mt-3 space-y-3">
                {/* Model picker */}
                <div className="space-y-1.5">
                  <label className="label">Model</label>
                  {!modelsData && <p className="text-xs text-gray-500">Loading models…</p>}
                  {localModels.map(m => (
                    <div
                      key={m.id}
                      className={`flex items-center gap-2 p-2 rounded border cursor-pointer text-xs transition-colors ${
                        aiModel === m.id ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                      }`}
                      onClick={() => { setAiModel(m.id); setAiStyle("detailed"); }}
                    >
                      <div className="flex-1 font-medium">{m.name}</div>
                      <span className="text-gray-500">{m.vram_mb / 1024}GB</span>
                      {m.loaded && <span className="badge-green">Loaded</span>}
                    </div>
                  ))}
                  {ollamaModels.length > 0 && (
                    <>
                      <p className="text-xs text-gray-500 pt-1">Ollama</p>
                      {ollamaModels.map(m => (
                        <div
                          key={m.id}
                          className={`flex items-center gap-2 p-2 rounded border cursor-pointer text-xs transition-colors ${
                            aiModel === m.id ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                          }`}
                          onClick={() => { setAiModel(m.id); setAiStyle("detailed"); }}
                        >
                          <div className="flex-1 font-medium">{m.name}</div>
                        </div>
                      ))}
                    </>
                  )}
                </div>

                {/* Style picker */}
                {aiModel && (
                  <div>
                    <label className="label">Style</label>
                    <div className="flex flex-wrap gap-1.5">
                      {aiStyles.map(s => (
                        <button
                          key={s}
                          className={`btn btn-sm ${aiStyle === s ? "btn-primary" : "btn-secondary"}`}
                          onClick={() => setAiStyle(s)}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Custom prompt */}
                {aiModel && (
                  <div>
                    <label className="label">Custom Prompt (optional)</label>
                    <textarea
                      className="input h-14 resize-none text-xs"
                      value={aiCustomPrompt}
                      onChange={e => setAiCustomPrompt(e.target.value)}
                      placeholder={
                        aiModel.startsWith("ollama:")
                          ? "Leave blank for style preset…"
                          : "Override the default prompt for this style…"
                      }
                    />
                  </div>
                )}

                <PromptPresetManager
                  currentModel={aiModel}
                  currentStyle={aiStyle}
                  currentPrompt={aiCustomPrompt}
                  onLoad={(p) => {
                    setAiModel(p.model);
                    setAiStyle(p.style);
                    setAiCustomPrompt(p.prompt);
                  }}
                />

                <ResolutionPicker
                  targetWidth={aiTargetWidth}
                  targetHeight={aiTargetHeight}
                  onChange={(w, h) => { setAiTargetWidth(w); setAiTargetHeight(h); }}
                />

                {/* Progress */}
                {aiJobId && aiJobProgress && (
                  <div className="space-y-1">
                    <div className="bg-gray-700 rounded-full h-1.5">
                      <div
                        className="bg-accent h-1.5 rounded-full transition-all"
                        style={{ width: `${aiJobProgress.percent ?? 0}%` }}
                      />
                    </div>
                    <p className="text-xs text-gray-500">{aiJobProgress.message || "Generating…"}</p>
                  </div>
                )}

                <button
                  className="btn-primary w-full flex items-center justify-center gap-2"
                  onClick={() => aiMutation.mutate()}
                  disabled={!aiModel || aiMutation.isPending || aiRunning}
                >
                  <Sparkles size={14} />
                  {aiRunning ? "Generating…" : "Generate Caption"}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
