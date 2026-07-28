import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { usePaneDatasetId, usePaneImageId } from "../hooks/usePaneDatasetId";
import { usePaneNavigate } from "../hooks/usePaneNavigate";
import { usePaneContext } from "../contexts/PaneContext";
import { usePaneStore } from "../store/paneStore";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, Save, Crop, AlertTriangle, Copy, Sparkles, ChevronDown, ChevronUp, Type, Eye, EyeOff, ScanSearch, Pencil, Maximize2, Palette, CheckSquare, Square, Crosshair, Combine, Focus, BoxSelect } from "lucide-react";
import Cropper from "react-easy-crop";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../utils/apiError";
import { imagesApi } from "../api/images";
import { videosApi } from "../api/videos";
import { captionsApi } from "../api/captions";
import { formatDuration } from "../utils/duration";
import { tagConsolidationApi } from "../api/tagConsolidation";
import { captioningApi, type DelimiterMode } from "../api/captioning";
import DelimiterControls from "../components/caption/DelimiterControls";
import { detectionApi } from "../api/detection";
import { upscalingApi } from "../api/upscaling";
import { lutApi } from "../api/lut";
import { useJobStore } from "../store/jobStore";
import { useSelectionStore } from "../store/selectionStore";
import PromptPresetManager from "../components/caption/PromptPresetManager";
import ResolutionPicker from "../components/caption/ResolutionPicker";
import GenerationMetadata from "../components/image/GenerationMetadata";
import ProvenancePanel from "../components/image/ProvenancePanel";
import ConfirmDialog from "../components/common/ConfirmDialog";
import type { ModelInfo, OllamaModel } from "../types";
import { type ProviderOut } from "../api/providers";
import ModelPicker from "../components/providers/ModelPicker";
import { STYLE_LABELS, modelType } from "../constants/captionStyles";
import { DINO_LAYER_LABELS } from "../constants/dinoLabels";
import { ASPECT_PRESETS } from "../constants/aspectRatios";
import { detectionModelFamily } from "../constants/detectionModels";
import CropToDetectionForm from "../components/crop/CropToDetectionForm";
import DetectionsPanel from "../components/detection/DetectionsPanel";
import { detectionCropPrefill } from "../utils/detectionCrop";
import { invalidateDetectionQueries } from "../utils/detectionQueries";
import type { Detection } from "../types";
import { getGalleryPageSize } from "../constants/storage";
import { useTokenCount } from "../utils/tokenCount";

interface Wd14ModelInfo { id: string; name: string; }

function resolveModelId(base: string, providerModel: string): string {
  if (base.startsWith("openai_compat:") && providerModel) return `${base}:${providerModel}`;
  return base;
}

const BBOX_COLORS = ["#f87171","#fb923c","#facc15","#4ade80","#34d399","#22d3ee","#818cf8","#c084fc","#f472b6","#94a3b8"];
function labelColor(label: string): string {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) & 0xffffffff;
  return BBOX_COLORS[Math.abs(h) % BBOX_COLORS.length];
}

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

function mutateNavIds(datasetId: string, transform: (ids: string[]) => string[]) {
  try {
    const raw = sessionStorage.getItem(`gallery-nav-${datasetId}`);
    if (!raw) return;
    const ctx = JSON.parse(raw) as { ids: string[]; [k: string]: unknown };
    sessionStorage.setItem(`gallery-nav-${datasetId}`, JSON.stringify({ ...ctx, ids: transform(ctx.ids) }));
  } catch { /* ignore */ }
}

function injectNavId(datasetId: string, afterId: string, newId: string) {
  mutateNavIds(datasetId, (ids) => {
    const next = [...ids];
    const idx = ids.indexOf(afterId);
    if (idx >= 0) next.splice(idx + 1, 0, newId);
    else next.push(newId);
    return next;
  });
}

function removeNavId(datasetId: string, removedId: string) {
  mutateNavIds(datasetId, (ids) => ids.filter(id => id !== removedId));
}

export default function ImageDetailPage() {
  const datasetId = usePaneDatasetId();
  const imageId = usePaneImageId();
  const { go: paneGo, back: paneBack } = usePaneNavigate();
  const qc = useQueryClient();
  const paneCtx = usePaneContext();
  const activePaneId = usePaneStore((s) => s.activePaneId);
  const isImageSelected = useSelectionStore((s) => (imageId ? s.isSelected(imageId) : false));
  const toggle = useSelectionStore((s) => s.toggle);

  const [captionText, setCaptionText] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const captionRef = useRef<HTMLTextAreaElement>(null);
  const [captionDirty, setCaptionDirty] = useState(false);
  const [captionDragActive, setCaptionDragActive] = useState(false);
  const [cropMode, setCropMode] = useState(false);
  const [cropReplace, setCropReplace] = useState(false);
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [aspect, setAspect] = useState<number | undefined>(undefined);
  const [croppedArea, setCroppedArea] = useState<CropArea | null>(null);
  const [outputWidth, setOutputWidth] = useState("");
  const [outputHeight, setOutputHeight] = useState("");
  // Crop + upscale (atomic)
  const [cropUpscaleModel, setCropUpscaleModel] = useState("");
  const [cropUpscaleTargetW, setCropUpscaleTargetW] = useState("");
  const [cropUpscaleTargetH, setCropUpscaleTargetH] = useState("");
  const [cropUpscaleJobId, setCropUpscaleJobId] = useState<string | null>(null);
  // Standalone upscale mode
  const [upscaleMode, setUpscaleMode] = useState(false);
  const [upscaleModel, setUpscaleModel] = useState("");
  const [upscaleReplace, setUpscaleReplace] = useState(false);
  const [upscaleTargetW, setUpscaleTargetW] = useState("");
  const [upscaleTargetH, setUpscaleTargetH] = useState("");
  const [upscaleJobId, setUpscaleJobId] = useState<string | null>(null);
  // LUT mode
  const [lutMode, setLutMode] = useState(false);
  const [lutPath, setLutPath] = useState("");
  const [lutIntensity, setLutIntensity] = useState(100);
  const [lutReplace, setLutReplace] = useState(false);
  const [lutJobId, setLutJobId] = useState<string | null>(null);

  const owNum = parseInt(outputWidth);
  const ohNum = parseInt(outputHeight);
  const effectiveAspect = (owNum > 0 && ohNum > 0) ? owNum / ohNum : aspect;

  // Detection state
  const [overlayVisible, setOverlayVisible] = useState(true);
  const [hiddenLabels, setHiddenLabels] = useState<Set<string>>(new Set());
  const [showDetectModal, setShowDetectModal] = useState(false);
  const [showCropDetect, setShowCropDetect] = useState(false);
  const [detectModel, setDetectModel] = useState("florence2_large");
  const [detectTask, setDetectTask] = useState("<OD>");
  const [detectPrompt, setDetectPrompt] = useState("");
  const [detectOverwrite, setDetectOverwrite] = useState(true);
  const [detectJobIds, setDetectJobIds] = useState<string[]>([]);
  const [detectMinProb, setDetectMinProb] = useState(0.5);
  const [samPoints, setSamPoints] = useState<{x: number; y: number; label: number}[]>([]);
  const [samPointMode, setSamPointMode] = useState(false);
  // Manual bbox drawing + mask refine
  const [drawMode, setDrawMode] = useState(false);
  const [drawRect, setDrawRect] = useState<{x1: number; y1: number; x2: number; y2: number} | null>(null);
  const drawStart = useRef<{x: number; y: number} | null>(null);
  const [pendingBox, setPendingBox] = useState<{x1: number; y1: number; x2: number; y2: number} | null>(null);
  const [manualLabel, setManualLabel] = useState("");
  const [manualRefineSam, setManualRefineSam] = useState(false);
  const [manualJobId, setManualJobId] = useState<string | null>(null);
  const [refineTarget, setRefineTarget] = useState<Detection | null>(null);
  const [refineJobId, setRefineJobId] = useState<string | null>(null);
  // Crop prefill (union of detections) — seeds react-easy-crop on fresh mount
  const [cropInitialArea, setCropInitialArea] = useState<CropArea | null>(null);
  // Bumped whenever the crop tool should re-read initialCroppedAreaPixels; used
  // as the Cropper's key to force a remount (react-easy-crop only reads the
  // initial area on mount). A counter, not a JSON key, so the *same* prefill
  // applied twice (e.g. Crop-from-Detections clicked again) still remounts.
  const [cropSeed, setCropSeed] = useState(0);

  // AI captioning state
  const [showAi, setShowAi] = useState(false);
  const [aiModel, setAiModel] = useState("");
  const [aiStyle, setAiStyle] = useState("detailed");
  const [aiCustomPrompt, setAiCustomPrompt] = useState("");
  const [aiTargetWidth, setAiTargetWidth] = useState<number | null>(null);
  const [aiTargetHeight, setAiTargetHeight] = useState<number | null>(null);
  const [aiJobId, setAiJobId] = useState<string | null>(null);
  const [aiProviderModel, setAiProviderModel] = useState("");
  const [aiWd14Threshold, setAiWd14Threshold] = useState(0.35);
  const [aiDelimiterMode, setAiDelimiterMode] = useState<DelimiterMode>("overwrite");
  const [aiDelimiterParts, setAiDelimiterParts] = useState<string[]>([",", " "]);

  const [renameMode, setRenameMode] = useState(false);
  const [renameStem, setRenameStem] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // GPT-2 token count from the lazily-loaded tokenizer (null until it loads).
  const tokens = useTokenCount(captionText);
  const captionStats = useMemo(() => {
    const trimmed = captionText.trim();
    const words = trimmed ? trimmed.split(/\s+/).length : 0;
    const tokenColor =
      tokens === null ? "text-gray-500"
      : tokens >= 77 ? "text-red-400"
      : tokens >= 70 ? "text-yellow-400"
      : "text-gray-500";
    return { words, tokens, tokenColor };
  }, [captionText, tokens]);

  const pageSize = getGalleryPageSize();

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
  const atEnd = !!navCtx && currentIndex === navCtx.ids.length - 1 && navCtx.ids.length === pageSize;
  const atStart = currentIndex === 0 && !!navCtx && navCtx.page > 1;

  const { data: nextPageData } = useQuery({
    queryKey: ["gallery-nav", datasetId, navCtx?.page, navCtx?.sort, navCtx?.order, navCtx?.captionedFilter, "next"],
    queryFn: () => imagesApi.list({
      dataset_id: datasetId!,
      page: navCtx!.page + 1,
      limit: pageSize,
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
      limit: pageSize,
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
          const raw = localStorage.getItem(`gallery-state-${datasetId}`);
          if (raw) {
            const state = JSON.parse(raw);
            localStorage.setItem(`gallery-state-${datasetId}`, JSON.stringify({ ...state, page: newCtx.page, scrollTop: 0 }));
          }
        } catch {}
      }
    }
    paneGo(`/datasets/${datasetId}/image/${id}`, { page: "image-detail", datasetId: datasetId ?? "", imageId: id }, { replace: true });
  }, [navCtx, datasetId, atEnd, atStart, nextId, prevId, nextPageData, prevPageData, paneGo]);

  // Mutually-exclusive annotation modes (draw / SAM points / mask refine).
  // Entering one clears the others' points/rect; passing null exits all.
  const enterMode = useCallback((mode: "draw" | "points" | "refine" | null, det?: Detection) => {
    setDrawMode(mode === "draw");
    setSamPointMode(mode === "points");
    setRefineTarget(mode === "refine" ? (det ?? null) : null);
    setDrawRect(null);
    setPendingBox(null);
    drawStart.current = null;
    // "points" accumulates its own clicks across toggles; draw/refine/exit start fresh.
    if (mode !== "points") setSamPoints([]);
  }, []);

  useEffect(() => {
    setHiddenLabels(new Set());
    setRenameMode(false);
    setRenameStem("");
    // Reset annotation modes when navigating to another image.
    setDrawMode(false);
    setSamPointMode(false);
    setSamPoints([]);
    setDrawRect(null);
    setPendingBox(null);
    setRefineTarget(null);
    setCropInitialArea(null);
  }, [imageId]);

  // Arrow-key navigation — skip when focus is inside a text field, a dialog is open,
  // or this pane is not the active pane in split-pane mode.
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (paneCtx && paneCtx.paneId !== activePaneId) return;
      if (showDeleteConfirm) return;
      const target = e.target as HTMLElement;
      const inTextField =
        target.tagName === "INPUT" || target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" || target.isContentEditable;
      // Escape exits annotation modes — but not while typing in a text field.
      // The draw-box label input has its own Escape handler (clears the pending
      // box only), and the caption editor's Escape must be left to the browser.
      if (e.key === "Escape" && !inTextField && (drawMode || refineTarget || pendingBox)) {
        setPendingBox(null);
        enterMode(null);
        return;
      }
      if (inTextField) return;
      if (e.key === " " && imageId && !showDetectModal) {
        e.preventDefault();
        toggle(imageId, datasetId ?? "");
        return;
      }
      if (e.key === "ArrowLeft" && prevId) goTo(prevId);
      if (e.key === "ArrowRight" && nextId) goTo(nextId);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [prevId, nextId, goTo, showDeleteConfirm, showDetectModal, toggle, imageId, paneCtx, activePaneId, drawMode, refineTarget, pendingBox, enterMode]);

  useEffect(() => {
    const anyModalOpen = showDetectModal || showDeleteConfirm;
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
  }, [showDetectModal, showDeleteConfirm]);

  const { data: image, isLoading: imageLoading, isError: imageError } = useQuery({
    queryKey: ["image", imageId],
    queryFn: () => imagesApi.get(imageId!),
    enabled: !!imageId,
    staleTime: 0,
    // A 404 (deleted/moved image) is terminal — don't burn retries on it. Anything
    // else keeps the app-wide budget from App.tsx (retry: 1); this override exists
    // only to add the 404 short-circuit, not to retry more.
    retry: (failureCount, err) =>
      (err as { response?: { status?: number } })?.response?.status === 404 ? false : failureCount < 1,
  });

  const { data: captionData } = useQuery({
    queryKey: ["caption", imageId],
    queryFn: () => captionsApi.get(imageId!),
    enabled: !!imageId,
  });

  // The video this frame was extracted from, for the lineage line below. Same
  // `["video", id]` key VideoDetailPage uses, so navigating there is a cache hit.
  // A frame keeps its timestamp and shot index after the video is deleted, but
  // `source_video_id` goes NULL — so this query simply stops running.
  const sourceVideoId = image?.source_video_id ?? null;
  const { data: sourceVideo } = useQuery({
    queryKey: ["video", sourceVideoId],
    queryFn: () => videosApi.get(sourceVideoId!),
    enabled: !!sourceVideoId,
    retry: false,
  });

  const { data: modelsData } = useQuery({
    queryKey: ["captioning-models"],
    queryFn: captioningApi.models,
    enabled: showAi,
  });

  const { data: upscaleModels = [] } = useQuery({
    queryKey: ["upscale-models"],
    queryFn: upscalingApi.models,
    enabled: upscaleMode || cropMode,
    staleTime: Infinity,
  });
  const upscaleModelOptions = upscaleModels.map((m) => (
    <option key={m.path} value={m.path}>{m.name}{m.scale ? ` (${m.scale}×)` : ""}</option>
  ));

  const { data: lutModels = [] } = useQuery({
    queryKey: ["lut-models"],
    queryFn: lutApi.models,
    staleTime: Infinity,
  });

  const localModels = (modelsData?.local_models ?? []) as ModelInfo[];
  const ollamaModels = (modelsData?.ollama_models ?? []) as OllamaModel[];
  const wd14Models = (modelsData?.wd14_models ?? []) as Wd14ModelInfo[];
  const providers = (modelsData?.openai_compat_models ?? []) as ProviderOut[];
  const aiModelType = modelType(aiModel);
  const aiStyles = aiModelType ? (STYLE_LABELS[aiModelType] ?? []) : [];

  const activeJobs = useJobStore((s) => s.activeJobs);

  // Track AI job progress from the global SSE store (TopBar already subscribes to all jobs)
  const aiJobProgress = useJobStore((s) => s.activeJobs.get(aiJobId ?? ""));

  // Upscale job progress
  const upscaleJobProgress = useJobStore((s) => s.activeJobs.get(upscaleJobId ?? ""));
  const cropUpscaleJobProgress = useJobStore((s) => s.activeJobs.get(cropUpscaleJobId ?? ""));
  const lutJobProgress = useJobStore((s) => s.activeJobs.get(lutJobId ?? ""));
  const manualJobProgress = useJobStore((s) => s.activeJobs.get(manualJobId ?? ""));
  const refineJobProgress = useJobStore((s) => s.activeJobs.get(refineJobId ?? ""));

  // Track a list of detection jobs so multiple runs can be queued (the modal
  // closes and SAM points clear at job start, not here). Iterate the tracked ids
  // each time activeJobs changes; drop any that reached a terminal status.
  useEffect(() => {
    if (detectJobIds.length === 0) return;
    const done: string[] = [];
    for (const jobId of detectJobIds) {
      const progress = activeJobs.get(jobId);
      if (!progress) continue;
      if (progress.status === "completed") {
        qc.invalidateQueries({ queryKey: ["image", imageId] });
        invalidateDetectionQueries(qc, datasetId);
        toast.success("Detection complete");
        done.push(jobId);
      } else if (progress.status === "failed") {
        toast.error(progress.message || "Detection failed");
        done.push(jobId);
      } else if (progress.status === "cancelled") {
        done.push(jobId);
      }
    }
    if (done.length > 0) {
      setDetectJobIds((prev) => prev.filter((id) => !done.includes(id)));
    }
  }, [activeJobs, detectJobIds, imageId, datasetId, qc]);

  // Manual box + SAM job completion (kept separate from the detect effect, which
  // closes the detect modal).
  useEffect(() => {
    if (!manualJobId || !manualJobProgress) return;
    if (manualJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["image", imageId] });
      invalidateDetectionQueries(qc, datasetId);
      setManualJobId(null);
      setPendingBox(null);
      toast.success("Detection added");
    } else if (manualJobProgress.status === "failed") {
      setManualJobId(null);
      toast.error(manualJobProgress.message || "Failed to add detection");
    }
  }, [manualJobProgress?.status, manualJobId, imageId, datasetId, qc]);

  // Mask refine job completion.
  useEffect(() => {
    if (!refineJobId || !refineJobProgress) return;
    if (refineJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["image", imageId] });
      invalidateDetectionQueries(qc, datasetId);
      setRefineJobId(null);
      enterMode(null);
      toast.success("Mask refined");
    } else if (refineJobProgress.status === "failed") {
      setRefineJobId(null);
      toast.error(refineJobProgress.message || "Refine failed");
    }
  }, [refineJobProgress?.status, refineJobId, imageId, datasetId, qc, enterMode]);

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

  // Standalone upscale job tracking
  useEffect(() => {
    if (!upscaleJobId || !upscaleJobProgress) return;
    if (upscaleJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["image", imageId] });
      setUpscaleJobId(null);
      if (!upscaleReplace && upscaleJobProgress.image_id) {
        if (datasetId && imageId) injectNavId(datasetId, imageId, upscaleJobProgress.image_id);
        toast.success("Upscaling complete — navigating to new image");
        setUpscaleMode(false);
        paneGo(
          `/datasets/${datasetId}/image/${upscaleJobProgress.image_id}`,
          { page: "image-detail", datasetId: datasetId ?? "", imageId: upscaleJobProgress.image_id },
          { replace: true },
        );
      } else {
        toast.success("Upscaling complete");
        setUpscaleMode(false);
      }
    } else if (upscaleJobProgress.status === "failed") {
      setUpscaleJobId(null);
      toast.error("Upscaling failed");
    }
  }, [upscaleJobProgress?.status, upscaleJobId, datasetId, imageId, upscaleReplace, qc, paneGo]);

  // LUT job tracking
  useEffect(() => {
    if (!lutJobId || !lutJobProgress) return;
    if (lutJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["image", imageId] });
      setLutJobId(null);
      if (!lutReplace && lutJobProgress.image_id) {
        if (datasetId && imageId) injectNavId(datasetId, imageId, lutJobProgress.image_id);
        toast.success("LUT applied — navigating to new image");
        setLutMode(false);
        paneGo(
          `/datasets/${datasetId}/image/${lutJobProgress.image_id}`,
          { page: "image-detail", datasetId: datasetId ?? "", imageId: lutJobProgress.image_id },
          { replace: true },
        );
      } else {
        toast.success("LUT applied");
        setLutMode(false);
      }
    } else if (lutJobProgress.status === "failed") {
      setLutJobId(null);
      toast.error("LUT grading failed");
    }
  }, [lutJobProgress?.status, lutJobId, datasetId, imageId, lutReplace, qc, paneGo]);

  // Crop+upscale job tracking
  useEffect(() => {
    if (!cropUpscaleJobId || !cropUpscaleJobProgress) return;
    if (cropUpscaleJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setCropUpscaleJobId(null);
      setCropMode(false);
      if (cropReplace) {
        qc.invalidateQueries({ queryKey: ["image", imageId] });
        toast.success("Crop & upscale applied");
      } else if (cropUpscaleJobProgress.image_id) {
        if (datasetId && imageId) injectNavId(datasetId, imageId, cropUpscaleJobProgress.image_id);
        toast.success(`Created upscaled crop`);
        paneGo(
          `/datasets/${datasetId}/image/${cropUpscaleJobProgress.image_id}`,
          { page: "image-detail", datasetId: datasetId ?? "", imageId: cropUpscaleJobProgress.image_id },
        );
      }
    } else if (cropUpscaleJobProgress.status === "failed") {
      setCropUpscaleJobId(null);
      toast.error("Crop+upscale failed");
    }
  }, [cropUpscaleJobProgress?.status, cropUpscaleJobId, datasetId, imageId, cropReplace, qc, paneGo]);

  useEffect(() => {
    if (captionData && !captionDirty) {
      setCaptionText(captionData.caption_text);
      setCaptionStyle(captionData.caption_style);
    }
  }, [captionData]);

  useEffect(() => {
    setCaptionDirty(false);
  }, [imageId]);

  useEffect(() => {
    const el = captionRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [captionText, imageId, image?.id]);

  const renameMutation = useMutation({
    mutationFn: () => imagesApi.renameImage(imageId!, renameStem),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["image", imageId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setRenameMode(false);
      toast.success("Renamed");
    },
    onError: () => toast.error("Rename failed"),
  });

  const deleteMutation = useMutation({
    mutationFn: () => imagesApi.batchDelete([imageId!]),
    onSuccess: () => {
      if (!datasetId) return;
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
      qc.invalidateQueries({ queryKey: ["tag-stats", datasetId] });
      qc.invalidateQueries({ queryKey: ["score-values", datasetId] });
      qc.invalidateQueries({ queryKey: ["tag-cooccurrence", datasetId] });
      qc.removeQueries({ queryKey: ["image", imageId] });
      if (imageId) removeNavId(datasetId, imageId);
      setShowDeleteConfirm(false);
      toast.success("Image deleted");
      if (nextId) {
        paneGo(`/datasets/${datasetId}/image/${nextId}`, { page: "image-detail", datasetId, imageId: nextId }, { replace: true });
      } else if (prevId) {
        paneGo(`/datasets/${datasetId}/image/${prevId}`, { page: "image-detail", datasetId, imageId: prevId }, { replace: true });
      } else {
        paneGo(`/datasets/${datasetId}/gallery`, { page: "gallery", datasetId }, { replace: true });
      }
    },
    onError: () => toast.error("Delete failed"),
  });

  const saveMutation = useMutation({
    mutationFn: () => captionsApi.update(imageId!, { caption_text: captionText, caption_style: captionStyle }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["caption", imageId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
      qc.invalidateQueries({ queryKey: ["tag-stats", datasetId] });
      qc.invalidateQueries({ queryKey: ["score-values", datasetId] });
      qc.invalidateQueries({ queryKey: ["tag-cooccurrence", datasetId] });
      setCaptionDirty(false);
      toast.success("Saved");
    },
    onError: () => toast.error("Save failed"),
  });

  // Drag a .txt file onto the caption box to apply it as the caption.
  const handleCaptionFileDrop = (e: React.DragEvent) => {
    setCaptionDragActive(false);
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length === 0) return;
    // Always consume the drop so the browser never navigates to / opens the file,
    // even when the dropped file isn't a .txt.
    e.preventDefault();
    const txt = files.find((f) => f.name.toLowerCase().endsWith(".txt"));
    if (!txt) return;
    txt.text()
      .then((text) => {
        const t = text.trim();
        // Keep the editor marked dirty until the save confirms, so a failed save
        // doesn't leave the box showing unsaved text labelled as saved.
        setCaptionText(t);
        setCaptionDirty(true);
        return captionsApi
          .update(imageId!, { caption_text: t, caption_style: captionStyle })
          .then(() => {
            setCaptionDirty(false);
            qc.invalidateQueries({ queryKey: ["caption", imageId] });
            qc.invalidateQueries({ queryKey: ["images", datasetId] });
            toast.success("Caption applied");
          });
      })
      .catch(() => toast.error("Failed to apply caption"));
  };

  const mergeTagsMutation = useMutation({
    mutationFn: async () => {
      // If the editor has unsaved edits, persist them first so subsumption runs on the
      // text the user sees (the backend operates on the stored caption).
      if (captionDirty) {
        await captionsApi.update(imageId!, { caption_text: captionText, caption_style: captionStyle });
      }
      return tagConsolidationApi.subsume(datasetId!, { image_ids: [imageId!], dry_run: false });
    },
    onSuccess: (data) => {
      setCaptionDirty(false); // allow the caption query refetch to refresh the textarea
      qc.invalidateQueries({ queryKey: ["caption", imageId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
      qc.invalidateQueries({ queryKey: ["tag-stats", datasetId] });
      qc.invalidateQueries({ queryKey: ["tag-cooccurrence", datasetId] });
      if (data.affected === 0) toast("No redundant tags found");
      else toast.success("Merged redundant tags");
    },
    onError: () => toast.error("Merge tags failed"),
  });

  const cropMutation = useMutation({
    mutationFn: () => {
      if (!croppedArea) throw new Error("No crop area");
      return imagesApi.crop(imageId!, {
        ...croppedArea,
        output_width: owNum > 0 ? owNum : undefined,
        output_height: ohNum > 0 ? ohNum : undefined,
        replace: cropReplace || undefined,
        upscale_model: cropUpscaleModel || undefined,
        upscale_target_width: cropUpscaleModel && parseInt(cropUpscaleTargetW) > 0 ? parseInt(cropUpscaleTargetW) : undefined,
        upscale_target_height: cropUpscaleModel && parseInt(cropUpscaleTargetH) > 0 ? parseInt(cropUpscaleTargetH) : undefined,
      });
    },
    onSuccess: (data) => {
      if (!datasetId) return;
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      if ("job_id" in data) {
        // Crop+upscale — async job
        setCropUpscaleJobId(data.job_id);
        toast(cropUpscaleModel ? "Upscaling crop…" : "Applying crop…");
      } else if (cropReplace) {
        // Replace mode: stay on the same image
        qc.invalidateQueries({ queryKey: ["image", imageId] });
        setCropMode(false);
        toast.success(`Crop applied (${data.width}×${data.height})`);
      } else {
        setCropMode(false);
        if (datasetId && imageId) injectNavId(datasetId, imageId, data.id);
        toast.success(`Created ${data.filename} (${data.width}×${data.height})`);
        paneGo(
          `/datasets/${datasetId}/image/${data.id}`,
          { page: "image-detail", datasetId, imageId: data.id },
        );
      }
    },
    onError: () => toast.error("Crop failed"),
  });

  const upscaleMutation = useMutation({
    mutationFn: () =>
      upscalingApi.run({
        dataset_id: datasetId!,
        image_ids: [imageId!],
        model_path: upscaleModel,
        replace: upscaleReplace,
        target_width: parseInt(upscaleTargetW) > 0 ? parseInt(upscaleTargetW) : null,
        target_height: parseInt(upscaleTargetH) > 0 ? parseInt(upscaleTargetH) : null,
      }),
    onSuccess: (data) => {
      if (data.job_id) {
        setUpscaleJobId(data.job_id);
        toast.success("Upscaling…");
      }
    },
    onError: () => toast.error("Failed to start upscaling"),
  });

  const lutMutation = useMutation({
    mutationFn: () =>
      lutApi.run({
        dataset_id: datasetId!,
        image_ids: [imageId!],
        lut_path: lutPath,
        intensity: lutIntensity / 100,
        replace: lutReplace,
      }),
    onSuccess: (data) => {
      if (data.job_id) {
        setLutJobId(data.job_id);
        toast.success("Applying LUT…");
      }
    },
    onError: () => toast.error("Failed to start LUT grading"),
  });

  const detectMutation = useMutation({
    mutationFn: () =>
      detectionApi.run({
        dataset_id: datasetId!,
        image_ids: [imageId!],
        model: detectModel,
        task: detectTask,
        custom_prompt: detectPrompt,
        overwrite: detectOverwrite,
        min_prob: detectMinProb,
        point_prompts: detectModel === "sam2" && detectTask === "points" && samPoints.length > 0
          ? samPoints.map((p) => [p.x, p.y])
          : undefined,
        point_labels: detectModel === "sam2" && detectTask === "points" && samPoints.length > 0
          ? samPoints.map((p) => p.label)
          : undefined,
      }),
    onSuccess: (data) => {
      if (data.job_id) {
        setDetectJobIds((prev) => [...prev, data.job_id!]);
        // Points were already sent in the payload; close the modal and clear
        // SAM point state so the user can queue another run immediately.
        setShowDetectModal(false);
        setSamPointMode(false);
        setSamPoints([]);
        toast.success("Detection queued");
      } else {
        toast("No images to process");
      }
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to start detection"));
    },
  });

  const createManualMutation = useMutation({
    mutationFn: (params: { bbox: number[]; label: string; refine_with_sam: boolean }) =>
      detectionApi.createManual({ image_id: imageId!, ...params }),
    onSuccess: (data) => {
      if ("job_id" in data) {
        // SAM segmentation queued — completion effect will refresh + clear.
        setManualJobId(data.job_id);
      } else {
        // Synchronous insert — refresh and keep drawing for multi-box annotation.
        qc.invalidateQueries({ queryKey: ["image", imageId] });
        invalidateDetectionQueries(qc, datasetId);
        setPendingBox(null);
        toast.success("Detection added");
      }
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to add detection"));
    },
  });

  const refineMutation = useMutation({
    mutationFn: (params: { id: number; point_prompts: number[][]; point_labels: number[] }) =>
      detectionApi.refine(params.id, { point_prompts: params.point_prompts, point_labels: params.point_labels }),
    onSuccess: (data) => setRefineJobId(data.job_id),
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Failed to start refine"));
    },
  });

  const aiMutation = useMutation({
    mutationFn: () =>
      captioningApi.run({
        dataset_id: datasetId!,
        image_ids: [imageId!],
        model: resolveModelId(aiModel, aiProviderModel),
        style: aiStyle,
        overwrite: true,
        custom_prompt: aiCustomPrompt,
        ...(aiTargetWidth && aiTargetHeight ? { target_width: aiTargetWidth, target_height: aiTargetHeight } : {}),
        ...(aiModel.startsWith("wd14:") ? { wd14_threshold: aiWd14Threshold } : {}),
        delimiter_mode: aiDelimiterMode,
        delimiter: aiDelimiterParts.join(""),
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

  const resetCrop = useCallback(() => {
    setZoom(1);
    setCrop({ x: 0, y: 0 });
  }, []);

  const onCropComplete = useCallback((_: unknown, croppedPixels: CropArea) => {
    setCroppedArea(croppedPixels);
  }, []);

  if (imageError && !image) {
    return (
      <div className="p-8" style={{ color: "var(--fg-mute)" }}>
        <p style={{ color: "var(--bad)", marginBottom: 12 }}>
          Image not found — it may have been deleted or moved.
        </p>
        <button
          className="btn-ghost btn-sm flex items-center gap-1.5"
          onClick={() =>
            paneGo(`/datasets/${datasetId}/gallery`, { page: "gallery", datasetId }, { replace: true })
          }
        >
          <ArrowLeft size={14} /> Back to gallery
        </button>
      </div>
    );
  }

  if (imageLoading || !image) {
    return <div className="p-8 text-gray-500">Loading...</div>;
  }

  const isDuplicate = image.quality_flags?.is_duplicate as boolean | undefined;
  const isBlurry = image.quality_flags?.is_blurry as boolean | undefined;
  const isUniform = image.quality_flags?.is_uniform as boolean | undefined;
  const hasWatermark = image.quality_flags?.has_watermark as boolean | undefined;
  const isNsfw = image.quality_flags?.is_nsfw as boolean | undefined;
  const aiRunning = !!aiJobId && aiJobProgress?.status === "running";
  const upscaleRunning = !!upscaleJobId && upscaleJobProgress?.status === "running";
  const cropUpscaleRunning = !!cropUpscaleJobId && cropUpscaleJobProgress?.status === "running";
  const lutRunning = !!lutJobId && lutJobProgress?.status === "running";
  const manualRunning = !!manualJobId && manualJobProgress?.status === "running";
  const refineRunning = !!refineJobId && refineJobProgress?.status === "running";
  const detections: Detection[] = image.detections ?? [];

  const onCropFromDetections = () => {
    const prefill = detectionCropPrefill(detections, hiddenLabels, image.width ?? 0, image.height ?? 0);
    if (!prefill) {
      toast("No visible detections to crop to");
      return;
    }
    enterMode(null);
    setCropInitialArea(prefill);
    setCropSeed((s) => s + 1);
    setUpscaleMode(false);
    setLutMode(false);
    resetCrop();
    setCropMode(true);
  };

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
            className={`btn-sm flex items-center gap-1.5 ${isImageSelected ? "btn-primary" : "btn-ghost"}`}
            onClick={() => toggle(imageId!, datasetId ?? "")}
            title={isImageSelected ? "Deselect (Space)" : "Select (Space)"}
          >
            {isImageSelected ? <CheckSquare size={14} /> : <Square size={14} />}
            {isImageSelected ? "Selected" : "Select"}
          </button>
          {(image?.detections?.length ?? 0) > 0 && !cropMode && (
            <button
              className={`btn-sm flex items-center gap-1.5 ${overlayVisible ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setOverlayVisible((v) => !v)}
              title={overlayVisible ? "Hide detection boxes" : "Show detection boxes"}
            >
              {overlayVisible ? <Eye size={14} /> : <EyeOff size={14} />}
              Boxes
            </button>
          )}
          {!cropMode && (
            <button
              className={`btn-sm flex items-center gap-1.5 ${drawMode ? "btn-primary" : "btn-ghost"}`}
              onClick={() => enterMode(drawMode ? null : "draw")}
              title={drawMode ? "Exit draw mode" : "Draw a detection box by hand"}
            >
              <BoxSelect size={14} /> {drawMode ? "Drawing" : "Draw Box"}
            </button>
          )}
          {!cropMode && detectModel === "sam2" && detectTask === "points" && (
            <button
              className={`btn-sm flex items-center gap-1.5 ${samPointMode ? "btn-primary" : "btn-ghost"}`}
              // Deactivating points mode must NOT clear the placed points — they
              // survive a plain toggle-off (invariant: points are cleared only by
              // the Clear button, switching to another mode, job completion, or
              // navigation). So toggle off with setSamPointMode(false), not
              // enterMode(null) which would wipe them.
              onClick={() => (samPointMode ? setSamPointMode(false) : enterMode("points"))}
              title={samPointMode ? "Deactivate point prompt mode (click=foreground, right-click=background)" : "Activate point prompt mode"}
            >
              <Crosshair size={14} />
              {samPointMode ? `Points (${samPoints.length})` : "SAM Points"}
            </button>
          )}
          {!cropMode && detectModel === "sam2" && detectTask === "points" && samPoints.length > 0 && (
            <button
              className="btn-sm btn-ghost"
              onClick={() => setSamPoints([])}
              title="Clear all SAM points"
              style={{ fontSize: 11 }}
            >
              Clear
            </button>
          )}
          <button
            className={`btn-sm flex items-center gap-1.5 ${cropMode ? "btn-primary" : "btn-ghost"}`}
            onClick={() => {
              setCropMode((v) => {
                if (!v) { resetCrop(); setUpscaleMode(false); setLutMode(false); setCropInitialArea(null); setCropSeed((s) => s + 1); enterMode(null); }
                return !v;
              });
            }}
          >
            <Crop size={14} /> {cropMode ? "Cancel Crop" : "Crop"}
          </button>
          {(image?.detections?.length ?? 0) > 0 && !cropMode && (
            <button
              className="btn-sm btn-ghost flex items-center gap-1.5"
              onClick={() => setShowCropDetect(true)}
              title="Crop this image to its detected subjects"
            >
              <Focus size={14} /> Crop to Subject
            </button>
          )}
          <button
            className={`btn-sm flex items-center gap-1.5 ${upscaleMode ? "btn-primary" : "btn-ghost"}`}
            onClick={() => { setUpscaleMode((v) => !v); setCropMode(false); setLutMode(false); enterMode(null); }}
            disabled={upscaleRunning || cropUpscaleRunning}
          >
            <Maximize2 size={14} /> {upscaleMode ? "Cancel" : "Upscale"}
          </button>
          <button
            className={`btn-sm flex items-center gap-1.5 ${lutMode ? "btn-primary" : "btn-ghost"}`}
            onClick={() => { setLutMode((v) => !v); setCropMode(false); setUpscaleMode(false); enterMode(null); }}
            disabled={lutRunning}
          >
            <Palette size={14} /> {lutMode ? "Cancel" : "LUT"}
          </button>
          {cropMode && (
            <>
              <select
                className="input w-28"
                value={aspect ?? ""}
                disabled={owNum > 0 && ohNum > 0}
                title={(owNum > 0 && ohNum > 0) ? "Aspect ratio set by W×H" : undefined}
                onChange={(e) => { setAspect(e.target.value ? Number(e.target.value) : undefined); resetCrop(); }}
              >
                <option value="">Free</option>
                {ASPECT_PRESETS.map(({ label, value }) => (
                  <option key={label} value={value}>{label}</option>
                ))}
              </select>
              <input
                type="range"
                min={1}
                max={10}
                step={0.05}
                value={zoom}
                onChange={(e) => setZoom(Number(e.target.value))}
                title={`Zoom ${zoom.toFixed(2)}×`}
                style={{ width: 80, accentColor: "var(--accent)" }}
              />
              <input
                type="number"
                min="1"
                className="input"
                style={{ width: 68 }}
                placeholder="W px"
                value={outputWidth}
                onChange={(e) => {
                  const newW = e.target.value;
                  setOutputWidth(newW);
                  if (parseInt(newW) > 0 && ohNum > 0) resetCrop();
                }}
              />
              <span style={{ color: "var(--fg-mute)", fontSize: 13 }}>×</span>
              <input
                type="number"
                min="1"
                className="input"
                style={{ width: 68 }}
                placeholder="H px"
                value={outputHeight}
                onChange={(e) => {
                  const newH = e.target.value;
                  setOutputHeight(newH);
                  if (owNum > 0 && parseInt(newH) > 0) resetCrop();
                }}
              />
              <label className="flex items-center gap-1.5 text-sm cursor-pointer select-none">
                <input
                  type="checkbox"
                  className="checkbox"
                  checked={cropReplace}
                  onChange={(e) => setCropReplace(e.target.checked)}
                />
                Replace
              </label>
              {/* Crop + upscale toggle */}
              {upscaleModels.length > 0 && (
                <div style={{ display: "flex", alignItems: "center", gap: 6, borderLeft: "1px solid var(--line)", paddingLeft: 8 }}>
                  <select
                    className="input"
                    style={{ width: 148 }}
                    value={cropUpscaleModel}
                    onChange={(e) => setCropUpscaleModel(e.target.value)}
                  >
                    <option value="">No upscale</option>
                    {upscaleModelOptions}
                  </select>
                  {cropUpscaleModel && (
                    <>
                      <input
                        type="number"
                        min="1"
                        className="input"
                        style={{ width: 64 }}
                        placeholder="W px"
                        value={cropUpscaleTargetW}
                        onChange={(e) => setCropUpscaleTargetW(e.target.value)}
                      />
                      <span style={{ color: "var(--fg-mute)", fontSize: 13 }}>×</span>
                      <input
                        type="number"
                        min="1"
                        className="input"
                        style={{ width: 64 }}
                        placeholder="H px"
                        value={cropUpscaleTargetH}
                        onChange={(e) => setCropUpscaleTargetH(e.target.value)}
                      />
                    </>
                  )}
                </div>
              )}
              <button
                className="btn-primary btn-sm"
                onClick={() => cropMutation.mutate()}
                disabled={!croppedArea || cropMutation.isPending || cropUpscaleRunning}
              >
                {(() => {
                  if (cropUpscaleRunning) return "Upscaling…";
                  if (cropMutation.isPending) return "Saving…";
                  if (cropUpscaleModel) return "Crop & Upscale";
                  return cropReplace ? "Apply Crop" : "Save Crop";
                })()}
              </button>
            </>
          )}
          {/* Standalone upscale controls */}
          {upscaleMode && (
            <>
              <select
                className="input"
                style={{ width: 160 }}
                value={upscaleModel}
                onChange={(e) => setUpscaleModel(e.target.value)}
              >
                <option value="">— select model —</option>
                {upscaleModelOptions}
              </select>
              <label className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={upscaleReplace}
                  onChange={(e) => setUpscaleReplace(e.target.checked)}
                />
                Replace
              </label>
              <input
                type="number"
                min="1"
                className="input"
                style={{ width: 68 }}
                placeholder="W px"
                value={upscaleTargetW}
                onChange={(e) => setUpscaleTargetW(e.target.value)}
              />
              <span style={{ color: "var(--fg-mute)", fontSize: 13 }}>×</span>
              <input
                type="number"
                min="1"
                className="input"
                style={{ width: 68 }}
                placeholder="H px"
                value={upscaleTargetH}
                onChange={(e) => setUpscaleTargetH(e.target.value)}
              />
              <button
                className="btn-primary btn-sm flex items-center gap-1.5"
                onClick={() => upscaleMutation.mutate()}
                disabled={!upscaleModel || upscaleRunning || upscaleMutation.isPending}
              >
                <Maximize2 size={13} />
                {upscaleRunning ? `${upscaleJobProgress?.percent?.toFixed(0) ?? 0}%` : "Run"}
              </button>
            </>
          )}
          {/* LUT controls */}
          {lutMode && (
            <>
              <select
                className="input"
                style={{ width: 160 }}
                value={lutPath}
                onChange={(e) => setLutPath(e.target.value)}
              >
                <option value="">— select LUT —</option>
                {lutModels.map((m) => (
                  <option key={m.path} value={m.path}>{m.name} ({m.format})</option>
                ))}
              </select>
              <label className="flex items-center gap-1.5 text-sm" style={{ minWidth: 80 }}>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={lutIntensity}
                  onChange={(e) => setLutIntensity(Number(e.target.value))}
                  style={{ width: 72 }}
                />
                {lutIntensity}%
              </label>
              <label className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={lutReplace}
                  onChange={(e) => setLutReplace(e.target.checked)}
                />
                Replace
              </label>
              <button
                className="btn-primary btn-sm flex items-center gap-1.5"
                onClick={() => lutMutation.mutate()}
                disabled={!lutPath || lutRunning || lutMutation.isPending}
              >
                <Palette size={13} />
                {lutRunning ? `${lutJobProgress?.percent?.toFixed(0) ?? 0}%` : "Run"}
              </button>
            </>
          )}

          {/* Pending manual box → label + optional SAM segmentation */}
          {pendingBox && !cropMode && (
            <>
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>New box:</span>
              <input
                className="input"
                style={{ width: 160 }}
                placeholder="Label"
                value={manualLabel}
                onChange={(e) => setManualLabel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && manualLabel.trim() && !manualRunning) {
                    createManualMutation.mutate({ bbox: [pendingBox.x1, pendingBox.y1, pendingBox.x2, pendingBox.y2], label: manualLabel.trim(), refine_with_sam: manualRefineSam });
                  }
                  if (e.key === "Escape") setPendingBox(null);
                }}
                autoFocus
              />
              <label className="flex items-center gap-1.5 text-sm cursor-pointer" title="Segment the drawn box with SAM 2 (GPU host only)">
                <input type="checkbox" checked={manualRefineSam} onChange={(e) => setManualRefineSam(e.target.checked)} />
                Refine with SAM
              </label>
              <button
                className="btn-primary btn-sm flex items-center gap-1.5"
                onClick={() => createManualMutation.mutate({ bbox: [pendingBox.x1, pendingBox.y1, pendingBox.x2, pendingBox.y2], label: manualLabel.trim(), refine_with_sam: manualRefineSam })}
                disabled={!manualLabel.trim() || manualRunning || createManualMutation.isPending}
              >
                {manualRunning ? "Segmenting…" : "Add"}
              </button>
              <button className="btn-ghost btn-sm" onClick={() => setPendingBox(null)} disabled={manualRunning}>Cancel</button>
            </>
          )}

          {/* Mask refine strip */}
          {refineTarget && !cropMode && (
            <>
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>
                Refining <strong style={{ color: "var(--fg)" }}>{refineTarget.label}</strong> — click fg / right-click bg ({samPoints.length})
              </span>
              <button
                className="btn-primary btn-sm"
                onClick={() => refineMutation.mutate({ id: refineTarget.id, point_prompts: samPoints.map((p) => [p.x, p.y]), point_labels: samPoints.map((p) => p.label) })}
                disabled={samPoints.length === 0 || refineRunning || refineMutation.isPending}
              >
                {refineRunning ? "Refining…" : "Apply"}
              </button>
              <button className="btn-ghost btn-sm" onClick={() => enterMode(null)} disabled={refineRunning}>Cancel</button>
            </>
          )}
        </div>

        <div className="flex-1 relative bg-black/40">
          {cropMode ? (
            <Cropper
              key={cropSeed}
              image={imagesApi.fileUrlVersioned(imageId!, image.updated_at)}
              crop={crop}
              zoom={zoom}
              aspect={effectiveAspect}
              initialCroppedAreaPixels={cropInitialArea ?? undefined}
              onCropChange={setCrop}
              onZoomChange={setZoom}
              onCropComplete={onCropComplete}
            />
          ) : (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ position: "relative", maxWidth: "100%", maxHeight: "100%", lineHeight: 0 }}>
                <img
                  src={imagesApi.fileUrlVersioned(imageId!, image.updated_at)}
                  alt={image.filename}
                  style={{ display: "block", maxWidth: "100%", maxHeight: "calc(100vh - 120px)", objectFit: "contain" }}
                />
                {((overlayVisible && detections.length > 0) || samPoints.length > 0 || drawRect || pendingBox || refineTarget) && image.width && image.height && (
                  <svg
                    viewBox={`0 0 ${image.width} ${image.height}`}
                    preserveAspectRatio="xMidYMid meet"
                    style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
                  >
                    {(() => {
                      const maxDim = Math.max(image.width!, image.height!);
                      const strokeW = maxDim * 0.004;
                      const fontSize = maxDim * 0.018;
                      const ptR = maxDim * 0.012;
                      // While refining, show only the target detection (others dimmed away).
                      const detBoxes = refineTarget
                        ? detections.filter((d) => d.id === refineTarget.id)
                        : overlayVisible ? detections.filter((det) => !hiddenLabels.has(det.label)) : [];
                      const live = drawRect ?? pendingBox;
                      return (
                        <>
                          {detBoxes.map((det) => {
                            const [x1, y1, x2, y2] = det.bbox;
                            const rx = x1 * image.width!;
                            const ry = y1 * image.height!;
                            const rw = (x2 - x1) * image.width!;
                            const rh = (y2 - y1) * image.height!;
                            const color = labelColor(det.label);
                            const maskOpacity = refineTarget ? 0.5 : 0.3;
                            return (
                              <g key={det.id}>
                                {det.mask && (() => {
                                  try {
                                    const parsed: {polygons: number[][][]} = JSON.parse(det.mask);
                                    return parsed.polygons.map((poly, pi) => {
                                      const pts = poly.map(([px, py]) =>
                                        `${px * image.width!},${py * image.height!}`
                                      ).join(" ");
                                      return <polygon key={pi} points={pts} fill={color} fillOpacity={maskOpacity} stroke="none" />;
                                    });
                                  } catch { return null; }
                                })()}
                                <rect x={rx} y={ry} width={rw} height={rh} fill="none" stroke={color} strokeWidth={strokeW} />
                                <rect x={rx} y={ry - fontSize * 1.4} width={rw} height={fontSize * 1.4} fill={color} opacity={0.85} />
                                <text x={rx + 4} y={ry - fontSize * 0.3} fill="black" fontSize={fontSize} fontWeight="600" fontFamily="system-ui,sans-serif">
                                  {det.label}
                                </text>
                              </g>
                            );
                          })}
                          {live && (() => {
                            const rx = Math.min(live.x1, live.x2) * image.width!;
                            const ry = Math.min(live.y1, live.y2) * image.height!;
                            const rw = Math.abs(live.x2 - live.x1) * image.width!;
                            const rh = Math.abs(live.y2 - live.y1) * image.height!;
                            return <rect x={rx} y={ry} width={rw} height={rh} fill="none" stroke="white" strokeWidth={strokeW} strokeDasharray={`${strokeW * 3} ${strokeW * 2}`} />;
                          })()}
                          {samPoints.map((pt, idx) => (
                            <g key={`sampt-${idx}`}>
                              <circle
                                cx={pt.x * image.width!}
                                cy={pt.y * image.height!}
                                r={ptR}
                                fill={pt.label === 1 ? "#4ade80" : "#f87171"}
                                stroke="white"
                                strokeWidth={strokeW * 0.6}
                              />
                            </g>
                          ))}
                        </>
                      );
                    })()}
                  </svg>
                )}
                {(samPointMode || refineTarget) && image.width && image.height && (
                  <div
                    style={{ position: "absolute", inset: 0, cursor: "crosshair", zIndex: 10 }}
                    onClick={(e) => {
                      const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                      const x = (e.clientX - rect.left) / rect.width;
                      const y = (e.clientY - rect.top) / rect.height;
                      setSamPoints((prev) => [...prev, { x: Math.round(x * 10000) / 10000, y: Math.round(y * 10000) / 10000, label: 1 }]);
                    }}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                      const x = (e.clientX - rect.left) / rect.width;
                      const y = (e.clientY - rect.top) / rect.height;
                      setSamPoints((prev) => [...prev, { x: Math.round(x * 10000) / 10000, y: Math.round(y * 10000) / 10000, label: 0 }]);
                    }}
                  />
                )}
                {drawMode && !pendingBox && image.width && image.height && (
                  <div
                    style={{ position: "absolute", inset: 0, cursor: "crosshair", zIndex: 10 }}
                    onMouseDown={(e) => {
                      const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                      const x = (e.clientX - rect.left) / rect.width;
                      const y = (e.clientY - rect.top) / rect.height;
                      drawStart.current = { x, y };
                      setDrawRect({ x1: x, y1: y, x2: x, y2: y });
                    }}
                    onMouseMove={(e) => {
                      if (!drawStart.current) return;
                      const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                      const x = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
                      const y = Math.min(Math.max((e.clientY - rect.top) / rect.height, 0), 1);
                      setDrawRect({ x1: drawStart.current.x, y1: drawStart.current.y, x2: x, y2: y });
                    }}
                    onMouseUp={() => {
                      const r = drawRect;
                      drawStart.current = null;
                      if (r && Math.abs(r.x2 - r.x1) >= 0.005 && Math.abs(r.y2 - r.y1) >= 0.005) {
                        setPendingBox({
                          x1: Math.min(r.x1, r.x2), y1: Math.min(r.y1, r.y2),
                          x2: Math.max(r.x1, r.x2), y2: Math.max(r.y1, r.y2),
                        });
                        setManualLabel("");
                      }
                      setDrawRect(null);
                    }}
                    onMouseLeave={() => { drawStart.current = null; setDrawRect(null); }}
                  />
                )}
              </div>
            </div>
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
            <span className="text-gray-500">Filename</span>
            <span className="min-w-0">
              {renameMode ? (
                <span className="flex items-center gap-1">
                  <input
                    className="input"
                    style={{ fontSize: 11, height: 22, padding: "0 4px", flex: 1, minWidth: 0 }}
                    value={renameStem}
                    onChange={(e) => setRenameStem(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && renameStem.trim()) renameMutation.mutate();
                      if (e.key === "Escape") setRenameMode(false);
                    }}
                    autoFocus
                  />
                  <button className="icon-btn" style={{ width: 20, height: 20 }} onClick={() => renameMutation.mutate()} disabled={!renameStem.trim() || renameMutation.isPending}>
                    <Save size={11} />
                  </button>
                  <button className="icon-btn" style={{ width: 20, height: 20 }} onClick={() => setRenameMode(false)}>✕</button>
                </span>
              ) : (
                <span className="flex items-center gap-1 min-w-0">
                  <span className="truncate font-mono" style={{ fontSize: 11 }}>{image.filename}</span>
                  {image.is_auto_named && <span className="badge dot" title="Auto-named from caption">auto</span>}
                  <button
                    className="icon-btn"
                    style={{ width: 18, height: 18, opacity: 0.5 }}
                    title="Rename"
                    onClick={() => { setRenameStem(image.filename.replace(/\.[^.]+$/, "")); setRenameMode(true); }}
                  >
                    <Pencil size={11} />
                  </button>
                </span>
              )}
            </span>
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
            {image.nsfw_score !== null && image.nsfw_score !== undefined && (
              <>
                <span className="text-gray-500">NSFW</span>
                <span className={isNsfw ? "text-red-400" : "text-gray-300"}>
                  {(image.nsfw_score * 100).toFixed(0)}%{isNsfw ? " ⚠" : ""}
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

          {/* Frame lineage. Without it, a frame moved out of its extraction
              subfolder can no longer say where it came from. */}
          {image.source_video_id && (
            <div className="text-xs text-gray-500 flex items-center gap-1 flex-wrap mt-1">
              <span>From</span>
              <button
                className="btn-ghost btn-sm"
                style={{ padding: "0 4px", fontSize: 11 }}
                onClick={() =>
                  paneGo(`/datasets/${datasetId}/video/${image.source_video_id}`, {
                    page: "video-detail", datasetId, videoId: image.source_video_id!,
                  })
                }
              >
                <span className="font-mono">{sourceVideo?.filename ?? "source video"}</span>
              </button>
              {image.source_timestamp_ms !== null && image.source_timestamp_ms !== undefined && (
                <span>· {formatDuration(image.source_timestamp_ms)}</span>
              )}
              {image.source_shot_index !== null && image.source_shot_index !== undefined && (
                <span>· shot {image.source_shot_index}</span>
              )}
            </div>
          )}

          {image.dino_layer_scores && Object.keys(image.dino_layer_scores).length > 0 ? (
            <DinoLayerBreakdown scores={image.dino_layer_scores} />
          ) : image.has_dino_layer_embeddings ? (
            <p className="text-[11px] text-fg opacity-50 mt-1">
              Per-layer embeddings stored — run style similarity with "All layers" to score them.
            </p>
          ) : null}

          {/* Quality flags */}
          {(isDuplicate === true || isBlurry === true || isUniform === true || hasWatermark === true || isNsfw === true) && (
            <div className="flex gap-2 flex-wrap mt-2">
              {isNsfw === true && <span className="badge-red flex items-center gap-1"><EyeOff size={10} />NSFW</span>}
              {isBlurry === true && <span className="badge-yellow flex items-center gap-1"><AlertTriangle size={10} />Blurry</span>}
              {isDuplicate === true && <span className="badge-yellow flex items-center gap-1"><Copy size={10} />Duplicate</span>}
              {isUniform === true && <span className="badge-orange flex items-center gap-1"><AlertTriangle size={10} />Near-uniform</span>}
              {hasWatermark === true && <span className="badge-blue flex items-center gap-1"><Type size={10} />Watermark</span>}
            </div>
          )}

          {/* Detections panel */}
          <DetectionsPanel
            imageId={imageId!}
            datasetId={datasetId ?? ""}
            detections={detections}
            hiddenLabels={hiddenLabels}
            onToggleLabel={(label) => setHiddenLabels((prev) => {
              const next = new Set(prev);
              if (next.has(label)) next.delete(label); else next.add(label);
              return next;
            })}
            onOpenDetectModal={() => setShowDetectModal(true)}
            onStartRefine={(det) => enterMode("refine", det)}
            onCropFromDetections={onCropFromDetections}
            refineTargetId={refineTarget?.id ?? null}
            busy={manualRunning || refineRunning}
          />

          {/* AI generation metadata */}
          {image.generation_metadata && (
            <GenerationMetadata metadata={image.generation_metadata} />
          )}

          {/* Source & license provenance */}
          {/* Remount on navigation: the panel's draft state is seeded when the
              editor opens and deliberately never re-synced from props, so without
              a key an open editor would carry the previous image's draft over. */}
          <ProvenancePanel key={image.id} image={image} />
        </div>

        {/* Caption */}
        <div className="p-4 flex-1 space-y-3">
          <h3 className="font-medium text-sm text-gray-300 uppercase tracking-wide">Caption</h3>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="label !mb-0">Caption Text</label>
              <span className={`text-xs tabular-nums ${captionStats.tokenColor}`}>
                {captionStats.words} words · {captionStats.tokens ?? "…"} tokens
              </span>
            </div>
            <textarea
              ref={captionRef}
              className="input resize-none overflow-hidden"
              style={{
                minHeight: "8rem",
                ...(captionDragActive
                  ? { borderColor: "var(--accent)", boxShadow: "0 0 0 1px var(--accent)", background: "var(--accent-glow)" }
                  : {}),
              }}
              value={captionText}
              onChange={(e) => { setCaptionText(e.target.value); setCaptionDirty(true); }}
              onDragOver={(e) => {
                if (Array.from(e.dataTransfer.items || []).some((it) => it.kind === "file")) {
                  e.preventDefault();
                  if (!captionDragActive) setCaptionDragActive(true);
                }
              }}
              onDragLeave={() => setCaptionDragActive(false)}
              onDrop={handleCaptionFileDrop}
              placeholder="Natural language description... (or drop a .txt file here)"
            />
          </div>

          <button
            className="btn-primary w-full flex items-center justify-center gap-2"
            onClick={() => saveMutation.mutate()}
            disabled={!captionDirty || saveMutation.isPending}
          >
            <Save size={14} /> Save
          </button>

          <button
            className="btn-ghost btn-sm w-full flex items-center justify-center gap-2"
            onClick={() => mergeTagsMutation.mutate()}
            disabled={mergeTagsMutation.isPending}
            title="Drop redundant tags (e.g. 'tail' when 'long tail' is present) and collapse duplicates"
          >
            <Combine size={14} /> Merge redundant tags
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
                      onClick={() => { setAiModel(m.id); setAiStyle("detailed"); setAiProviderModel(""); }}
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
                          onClick={() => { setAiModel(m.id); setAiStyle("detailed"); setAiProviderModel(""); }}
                        >
                          <div className="flex-1 font-medium">{m.name}</div>
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
                          className={`flex items-center gap-2 p-2 rounded border cursor-pointer text-xs transition-colors ${
                            aiModel === m.id ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                          }`}
                          onClick={() => { setAiModel(m.id); setAiStyle("detailed"); setAiProviderModel(""); }}
                        >
                          <div className="flex-1 font-medium">{m.name}</div>
                        </div>
                      ))}
                    </>
                  )}
                  {providers.filter(p => !p.is_remote).length > 0 && (
                    <>
                      <p className="text-xs text-gray-500 pt-1">Local Providers</p>
                      {providers.filter(p => !p.is_remote).map(p => {
                        const baseId = `openai_compat:${p.id}`;
                        const isSelected = aiModel.startsWith(baseId);
                        return (
                          <div key={p.id}>
                            <div
                              className={`flex items-center gap-2 p-2 rounded border cursor-pointer text-xs transition-colors ${
                                isSelected ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                              }`}
                              onClick={() => { setAiModel(baseId); setAiProviderModel(p.default_model); }}
                            >
                              <div className="flex-1 font-medium">{p.name}</div>
                              <span className="text-gray-500 truncate max-w-[100px]">{p.base_url}</span>
                            </div>
                            {isSelected && (
                              <div className="mt-1 ml-2" onClick={e => e.stopPropagation()}>
                                <ModelPicker
                                  value={aiProviderModel}
                                  onChange={setAiProviderModel}
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
                        const isSelected = aiModel.startsWith(baseId);
                        return (
                          <div key={p.id}>
                            <div
                              className={`flex items-center gap-2 p-2 rounded border cursor-pointer text-xs transition-colors ${
                                isSelected ? "border-accent bg-accent/10" : "border-gray-700 hover:border-gray-500"
                              }`}
                              onClick={() => { setAiModel(baseId); setAiProviderModel(p.default_model); }}
                            >
                              <div className="flex-1 font-medium">{p.name}</div>
                              <span className="text-gray-500 truncate max-w-[100px]">{p.base_url}</span>
                            </div>
                            {isSelected && (
                              <div className="mt-1 ml-2" onClick={e => e.stopPropagation()}>
                                <ModelPicker
                                  value={aiProviderModel}
                                  onChange={setAiProviderModel}
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

                {/* WD14 threshold / style picker / custom prompt */}
                {aiModel && aiModel.startsWith("wd14:") && (
                  <div>
                    <label className="label">Tag Threshold</label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range" min={0} max={1} step={0.05}
                        value={aiWd14Threshold}
                        onChange={e => setAiWd14Threshold(Number(e.target.value))}
                        className="flex-1"
                      />
                      <span className="text-xs text-gray-400 w-8 text-right">{aiWd14Threshold.toFixed(2)}</span>
                    </div>
                  </div>
                )}

                {/* Style picker */}
                {aiModel && !aiModel.startsWith("wd14:") && aiStyles.length > 0 && (
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
                {aiModel && !aiModel.startsWith("wd14:") && (
                  <div>
                    <label className="label">Custom Prompt (optional)</label>
                    <textarea
                      className="input h-14 resize-none text-xs"
                      value={aiCustomPrompt}
                      onChange={e => setAiCustomPrompt(e.target.value)}
                      placeholder={
                        aiModel.startsWith("ollama:") || aiModel.startsWith("openai_compat:")
                          ? "Leave blank for style preset…"
                          : "Override the default prompt for this style…"
                      }
                    />
                  </div>
                )}

                {aiModel && !aiModel.startsWith("wd14:") && (
                  <>
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
                  </>
                )}

                {aiModel && (
                  <DelimiterControls
                    mode={aiDelimiterMode}
                    delimiterParts={aiDelimiterParts}
                    onChange={(m, parts) => { setAiDelimiterMode(m); setAiDelimiterParts(parts); }}
                  />
                )}

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

      {showDeleteConfirm && (
        <ConfirmDialog
          title="Delete Image"
          message="This will permanently delete this image and its caption."
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}

      {/* Detection modal */}
      {showDetectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-sm space-y-4">
            <h4 className="font-medium flex items-center gap-2">
              <ScanSearch size={16} /> Run Detection
            </h4>

            <div>
              <label className="label">Model</label>
              <select className="select w-full" value={detectModel} onChange={(e) => {
                const m = e.target.value;
                const familyChanged = detectionModelFamily(m) !== detectionModelFamily(detectModel);
                setDetectModel(m);
                if (familyChanged) {
                  // Reset task/prompt only across families (Florence ↔ SAM ↔ NudeNet).
                  if (m === "nudenet") setDetectTask("nudenet");
                  else if (m === "sam2" || m === "sam3") setDetectTask("text_prompt");
                  else setDetectTask("<OD>");
                  setDetectPrompt("");
                } else if (m === "sam3" && detectTask === "points") {
                  // Same family (sam2 → sam3) but sam3 has no points task — force text.
                  setDetectTask("text_prompt");
                }
              }}>
                <option value="florence2_large">Florence-2 Large</option>
                <option value="florence2_promptgen">Florence-2 PromptGen</option>
                <option value="nudenet">NudeNet (body-part detection)</option>
                <option value="sam2">SAM 2.1 + Grounding DINO (segmentation)</option>
                <option value="sam3">SAM 3 (text-prompt segmentation)</option>
              </select>
            </div>

            {detectModel === "nudenet" && (
              <div className="space-y-1">
                <label className="label">Min confidence: {detectMinProb.toFixed(2)}</label>
                <input
                  type="range"
                  min="0.1" max="1" step="0.05"
                  value={detectMinProb}
                  onChange={(e) => setDetectMinProb(parseFloat(e.target.value))}
                  style={{ width: "100%" }}
                />
                <p className="text-xs text-gray-500">Only body-part regions above this confidence score are stored.</p>
              </div>
            )}

            {detectModel === "sam2" && (
              <div className="space-y-2">
                <label className="label">Mode</label>
                <div className="flex gap-3">
                  {[
                    { value: "text_prompt", label: "Text prompt" },
                    { value: "points", label: "Point prompts" },
                  ].map((opt) => (
                    <label key={opt.value} className="flex items-center gap-1.5 cursor-pointer text-sm">
                      <input
                        type="radio"
                        name="sam2-task"
                        value={opt.value}
                        checked={detectTask === opt.value}
                        onChange={() => { setDetectTask(opt.value); setDetectPrompt(""); }}
                      />
                      {opt.label}
                    </label>
                  ))}
                </div>
                {detectTask === "text_prompt" && (
                  <div className="space-y-1">
                    <label className="label">Text prompt</label>
                    <input
                      className="input"
                      placeholder="e.g. face, hand, watermark"
                      value={detectPrompt}
                      onChange={(e) => setDetectPrompt(e.target.value)}
                      autoFocus
                    />
                    <p className="text-xs text-gray-500">Grounding DINO will locate matching regions; SAM2 will produce precise masks. Separate multiple phrases with commas.</p>
                  </div>
                )}
                {detectTask === "points" && (
                  <p className="text-xs text-gray-500">
                    Close this dialog, use the <strong>SAM Points</strong> toolbar button to place foreground points (left-click) and background points (right-click), then open this dialog again to run.
                    {samPoints.length > 0 && <span className="text-green-400"> {samPoints.length} point(s) placed.</span>}
                  </p>
                )}
              </div>
            )}

            {detectModel === "sam3" && (
              <div className="space-y-1">
                <label className="label">Text prompt</label>
                <input
                  className="input"
                  placeholder="e.g. face, hand, watermark"
                  value={detectPrompt}
                  onChange={(e) => setDetectPrompt(e.target.value)}
                  autoFocus
                />
                <p className="text-xs text-gray-500">SAM 3 finds and masks every instance of each phrase in one pass. Separate multiple phrases with commas.</p>
              </div>
            )}

            {detectModel !== "nudenet" && detectModel !== "sam2" && detectModel !== "sam3" && (
              <>
                <div>
                  <label className="label">Task</label>
                  <select
                    className="select w-full"
                    value={detectTask}
                    onChange={(e) => { setDetectTask(e.target.value); setDetectPrompt(""); }}
                  >
                    <option value="<OD>">Object Detection (auto-detect everything)</option>
                    <option value="<CAPTION_TO_PHRASE_GROUNDING>">Grounded Caption (draw boxes around phrases)</option>
                  </select>
                </div>

                {detectTask === "<CAPTION_TO_PHRASE_GROUNDING>" && (
                  <div className="space-y-2">
                    {captionText && (
                      <label className="flex items-center gap-2 cursor-pointer text-sm">
                        <input
                          type="checkbox"
                          checked={detectPrompt === captionText}
                          onChange={(e) => setDetectPrompt(e.target.checked ? captionText : "")}
                        />
                        Use this image's caption
                      </label>
                    )}
                    <label className="label">Caption to ground</label>
                    <input
                      className="input"
                      placeholder="e.g. a cat sitting on a dog"
                      value={detectPrompt}
                      onChange={(e) => setDetectPrompt(e.target.value)}
                      autoFocus={!captionText}
                    />
                    <p className="text-xs text-gray-500">
                      Florence-2 will draw boxes around the phrases from this caption.
                    </p>
                  </div>
                )}
              </>
            )}

            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input type="checkbox" checked={detectOverwrite} onChange={e => setDetectOverwrite(e.target.checked)} />
              Overwrite this model's existing detections
            </label>

            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setShowDetectModal(false)}>Cancel</button>
              <button
                className="btn-primary flex items-center gap-2"
                onClick={() => detectMutation.mutate()}
                disabled={
                  detectMutation.isPending ||
                  (detectTask === "<CAPTION_TO_PHRASE_GROUNDING>" && !detectPrompt.trim()) ||
                  ((detectModel === "sam2" || detectModel === "sam3") && detectTask === "text_prompt" && !detectPrompt.trim()) ||
                  (detectModel === "sam2" && detectTask === "points" && samPoints.length === 0)
                }

              >
                <ScanSearch size={14} /> Run Detection
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Crop-to-detection modal */}
      {showCropDetect && datasetId && imageId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="card p-5 w-full max-w-md space-y-1 max-h-[80vh] overflow-y-auto">
            <h4 className="font-medium flex items-center gap-2 mb-1">
              <Crop size={15} /> Crop to Detection
            </h4>
            <CropToDetectionForm
              datasetId={datasetId}
              imageIds={[imageId]}
              availableLabels={Object.entries(
                (image?.detections ?? []).reduce((acc, d) => {
                  acc[d.label] = (acc[d.label] ?? 0) + 1;
                  return acc;
                }, {} as Record<string, number>)
              )
                .sort((a, b) => b[1] - a[1])
                .map(([label, count]) => ({ label, count }))}
              onSuccess={() => {
                qc.invalidateQueries({ queryKey: ["image", imageId] });
                setShowCropDetect(false);
              }}
              onCancel={() => setShowCropDetect(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
