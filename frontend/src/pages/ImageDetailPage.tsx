import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { usePaneDatasetId, usePaneImageId } from "../hooks/usePaneDatasetId";
import { usePaneNavigate } from "../hooks/usePaneNavigate";
import { usePaneContext } from "../contexts/PaneContext";
import { usePaneStore } from "../stores/paneStore";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, Save, Crop, AlertTriangle, Copy, Sparkles, ChevronDown, ChevronUp, Type, Eye, EyeOff, ScanSearch, Pencil, Maximize2, Palette, CheckSquare, Square } from "lucide-react";
import Cropper from "react-easy-crop";
import toast from "react-hot-toast";
import { imagesApi } from "../api/images";
import { captionsApi } from "../api/captions";
import { captioningApi } from "../api/captioning";
import { detectionApi } from "../api/detection";
import { upscalingApi } from "../api/upscaling";
import { lutApi } from "../api/lut";
import { useJobStore } from "../store/jobStore";
import { useSelectionStore } from "../store/selectionStore";
import PromptPresetManager from "../components/caption/PromptPresetManager";
import ResolutionPicker from "../components/caption/ResolutionPicker";
import GenerationMetadata from "../components/image/GenerationMetadata";
import ConfirmDialog from "../components/common/ConfirmDialog";
import type { ModelInfo, OllamaModel } from "../types";
import { STYLE_LABELS, modelType } from "../constants/captionStyles";
import { DINO_LAYER_LABELS } from "../constants/dinoLabels";
import { encode } from "gpt-tokenizer";

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

  const [tags, setTags] = useState<string[]>([]);
  const [captionText, setCaptionText] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const captionRef = useRef<HTMLTextAreaElement>(null);
  const [captionDirty, setCaptionDirty] = useState(false);
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
  const [showDetectPanel, setShowDetectPanel] = useState(true);
  const [showDetectModal, setShowDetectModal] = useState(false);
  const [detectModel, setDetectModel] = useState("florence2_large");
  const [detectTask, setDetectTask] = useState("<OD>");
  const [detectPrompt, setDetectPrompt] = useState("");
  const [detectOverwrite, setDetectOverwrite] = useState(true);
  const [detectJobId, setDetectJobId] = useState<string | null>(null);

  // AI captioning state
  const [showAi, setShowAi] = useState(false);
  const [aiModel, setAiModel] = useState("");
  const [aiStyle, setAiStyle] = useState("detailed");
  const [aiCustomPrompt, setAiCustomPrompt] = useState("");
  const [aiTargetWidth, setAiTargetWidth] = useState<number | null>(null);
  const [aiTargetHeight, setAiTargetHeight] = useState<number | null>(null);
  const [aiJobId, setAiJobId] = useState<string | null>(null);

  const [renameMode, setRenameMode] = useState(false);
  const [renameStem, setRenameStem] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const captionStats = useMemo(() => {
    const trimmed = captionText.trim();
    const words = trimmed ? trimmed.split(/\s+/).length : 0;
    const tokens = trimmed ? encode(trimmed).length : 0;
    const tokenColor = tokens >= 77 ? "text-red-400" : tokens >= 70 ? "text-yellow-400" : "text-gray-500";
    return { words, tokens, tokenColor };
  }, [captionText]);

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

  useEffect(() => {
    setHiddenLabels(new Set());
    setRenameMode(false);
    setRenameStem("");
  }, [imageId]);

  // Arrow-key navigation — skip when focus is inside a text field, a dialog is open,
  // or this pane is not the active pane in split-pane mode.
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (paneCtx && paneCtx.paneId !== activePaneId) return;
      if (showDeleteConfirm) return;
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable) return;
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
  }, [prevId, nextId, goTo, showDeleteConfirm, showDetectModal, toggle, imageId, paneCtx, activePaneId]);

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

  const { data: image, isLoading: imageLoading } = useQuery({
    queryKey: ["image", imageId],
    queryFn: () => imagesApi.get(imageId!),
    enabled: !!imageId,
    staleTime: 0,
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
  const aiModelType = modelType(aiModel);
  const aiStyles = aiModelType ? (STYLE_LABELS[aiModelType] ?? []) : [];

  const detectJobProgress = useJobStore((s) => s.activeJobs.get(detectJobId ?? ""));

  // Track AI job progress from the global SSE store (TopBar already subscribes to all jobs)
  const aiJobProgress = useJobStore((s) => s.activeJobs.get(aiJobId ?? ""));

  // Upscale job progress
  const upscaleJobProgress = useJobStore((s) => s.activeJobs.get(upscaleJobId ?? ""));
  const cropUpscaleJobProgress = useJobStore((s) => s.activeJobs.get(cropUpscaleJobId ?? ""));
  const lutJobProgress = useJobStore((s) => s.activeJobs.get(lutJobId ?? ""));

  useEffect(() => {
    if (!detectJobId || !detectJobProgress) return;
    if (detectJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["image", imageId] });
      setDetectJobId(null);
      setShowDetectModal(false);
      toast.success("Detection complete");
    } else if (detectJobProgress.status === "failed") {
      setDetectJobId(null);
      toast.error("Detection failed");
    }
  }, [detectJobProgress?.status, detectJobId, imageId, qc]);

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
    mutationFn: () => captionsApi.update(imageId!, { caption_text: captionText, tags, caption_style: captionStyle }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["caption", imageId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
      setCaptionDirty(false);
      toast.success("Saved");
    },
    onError: () => toast.error("Save failed"),
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
      }),
    onSuccess: (data) => {
      if (data.job_id) {
        setDetectJobId(data.job_id);
      } else {
        toast("No images to process");
      }
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg ?? "Failed to start detection");
    },
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

  const resetCrop = useCallback(() => {
    setZoom(1);
    setCrop({ x: 0, y: 0 });
  }, []);

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
  const upscaleRunning = !!upscaleJobId && upscaleJobProgress?.status === "running";
  const cropUpscaleRunning = !!cropUpscaleJobId && cropUpscaleJobProgress?.status === "running";
  const lutRunning = !!lutJobId && lutJobProgress?.status === "running";

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
          <button
            className={`btn-sm flex items-center gap-1.5 ${cropMode ? "btn-primary" : "btn-ghost"}`}
            onClick={() => {
              setCropMode((v) => {
                if (!v) { resetCrop(); setUpscaleMode(false); setLutMode(false); }
                return !v;
              });
            }}
          >
            <Crop size={14} /> {cropMode ? "Cancel Crop" : "Crop"}
          </button>
          <button
            className={`btn-sm flex items-center gap-1.5 ${upscaleMode ? "btn-primary" : "btn-ghost"}`}
            onClick={() => { setUpscaleMode((v) => !v); setCropMode(false); setLutMode(false); }}
            disabled={upscaleRunning || cropUpscaleRunning}
          >
            <Maximize2 size={14} /> {upscaleMode ? "Cancel" : "Upscale"}
          </button>
          <button
            className={`btn-sm flex items-center gap-1.5 ${lutMode ? "btn-primary" : "btn-ghost"}`}
            onClick={() => { setLutMode((v) => !v); setCropMode(false); setUpscaleMode(false); }}
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
                <option value={1}>1:1</option>
                <option value={4/3}>4:3</option>
                <option value={16/9}>16:9</option>
                <option value={3/2}>3:2</option>
                <option value={9/16}>9:16</option>
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
        </div>

        <div className="flex-1 relative bg-black/40">
          {cropMode ? (
            <Cropper
              image={imagesApi.fileUrlVersioned(imageId!, image.updated_at)}
              crop={crop}
              zoom={zoom}
              aspect={effectiveAspect}
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
                {overlayVisible && (image.detections?.length ?? 0) > 0 && image.width && image.height && (
                  <svg
                    viewBox={`0 0 ${image.width} ${image.height}`}
                    preserveAspectRatio="xMidYMid meet"
                    style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
                  >
                    {(() => {
                      const maxDim = Math.max(image.width!, image.height!);
                      const strokeW = maxDim * 0.004;
                      const fontSize = maxDim * 0.018;
                      return image.detections.filter(det => !hiddenLabels.has(det.label)).map((det) => {
                        const [x1, y1, x2, y2] = det.bbox;
                        const rx = x1 * image.width!;
                        const ry = y1 * image.height!;
                        const rw = (x2 - x1) * image.width!;
                        const rh = (y2 - y1) * image.height!;
                        const color = labelColor(det.label);
                        return (
                          <g key={det.id}>
                            <rect x={rx} y={ry} width={rw} height={rh} fill="none" stroke={color} strokeWidth={strokeW} />
                            <rect x={rx} y={ry - fontSize * 1.4} width={rw} height={fontSize * 1.4} fill={color} opacity={0.85} />
                            <text x={rx + 4} y={ry - fontSize * 0.3} fill="black" fontSize={fontSize} fontWeight="600" fontFamily="system-ui,sans-serif">
                              {det.label}
                            </text>
                          </g>
                        );
                      });
                    })()}
                  </svg>
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

          {/* Detections panel */}
          <div style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
            <button
              style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", justifyContent: "space-between", padding: "2px 0", background: "none", border: "none", cursor: "pointer", color: "inherit" }}
              onClick={() => setShowDetectPanel((v) => !v)}
            >
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--fg-mute)", textTransform: "uppercase", letterSpacing: "0.05em", display: "flex", alignItems: "center", gap: 5 }}>
                <ScanSearch size={11} /> Detections
                {(image.detections?.length ?? 0) > 0 && (
                  <span style={{ fontWeight: 400, textTransform: "none" }}>({image.detections.length})</span>
                )}
              </span>
              {showDetectPanel ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            </button>

            {showDetectPanel && (
              <div style={{ marginTop: 6 }}>
                {(image.detections?.length ?? 0) > 0 ? (
                  <>
                    <p style={{ fontSize: 10, color: "var(--fg-mute)", marginBottom: 6 }}>
                      {image.detections[0].model} · {image.detections[0].task === "<OD>" ? "Object Detection" : "Grounded Caption"}
                    </p>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {Object.entries(
                        image.detections.reduce((acc, d) => {
                          acc[d.label] = (acc[d.label] ?? 0) + 1;
                          return acc;
                        }, {} as Record<string, number>)
                      )
                        .sort((a, b) => b[1] - a[1])
                        .map(([label, count]) => {
                          const hidden = hiddenLabels.has(label);
                          return (
                            <button
                              key={label}
                              onClick={() => setHiddenLabels(prev => {
                                const next = new Set(prev);
                                next.has(label) ? next.delete(label) : next.add(label);
                                return next;
                              })}
                              title={hidden ? "Show boxes" : "Hide boxes"}
                              style={{
                                fontSize: 11,
                                padding: "2px 7px",
                                borderRadius: 4,
                                background: hidden ? "transparent" : labelColor(label) + "33",
                                border: `1px solid ${hidden ? labelColor(label) + "44" : labelColor(label) + "88"}`,
                                color: hidden ? "var(--fg-mute)" : "var(--fg)",
                                whiteSpace: "nowrap",
                                cursor: "pointer",
                                opacity: hidden ? 0.45 : 1,
                                textDecoration: hidden ? "line-through" : "none",
                              }}
                            >
                              {label}{count > 1 && <span style={{ opacity: 0.6, marginLeft: 3 }}>×{count}</span>}
                            </button>
                          );
                        })}
                    </div>
                  </>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <p style={{ fontSize: 11, color: "var(--fg-mute)" }}>No detections run yet.</p>
                    <button
                      className="btn btn-ghost btn-sm"
                      style={{ alignSelf: "flex-start", display: "flex", alignItems: "center", gap: 5 }}
                      onClick={() => setShowDetectModal(true)}
                    >
                      <ScanSearch size={12} /> Run Detection
                    </button>
                  </div>
                )}
                {(image.detections?.length ?? 0) > 0 && (
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 5, fontSize: 11 }}
                    onClick={() => setShowDetectModal(true)}
                  >
                    <ScanSearch size={12} /> Re-run Detection
                  </button>
                )}
              </div>
            )}
          </div>

          {/* AI generation metadata */}
          {image.generation_metadata && (
            <GenerationMetadata metadata={image.generation_metadata} />
          )}
        </div>

        {/* Caption */}
        <div className="p-4 flex-1 space-y-3">
          <h3 className="font-medium text-sm text-gray-300 uppercase tracking-wide">Caption</h3>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="label !mb-0">Caption Text</label>
              <span className={`text-xs tabular-nums ${captionStats.tokenColor}`}>
                {captionStats.words} words · {captionStats.tokens} tokens
              </span>
            </div>
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
              <select className="select w-full" value={detectModel} onChange={(e) => setDetectModel(e.target.value)}>
                <option value="florence2_large">Florence-2 Large</option>
                <option value="florence2_promptgen">Florence-2 PromptGen</option>
              </select>
            </div>

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

            {detectJobId && detectJobProgress && (
              <div className="space-y-1">
                <div className="bg-gray-700 rounded-full h-1.5">
                  <div className="bg-accent h-1.5 rounded-full transition-all" style={{ width: `${detectJobProgress.percent ?? 0}%` }} />
                </div>
                <p className="text-xs text-gray-500">{detectJobProgress.message || "Detecting…"}</p>
              </div>
            )}

            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input type="checkbox" checked={detectOverwrite} onChange={e => setDetectOverwrite(e.target.checked)} />
              Overwrite existing detections
            </label>

            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setShowDetectModal(false)} disabled={!!detectJobId}>Cancel</button>
              <button
                className="btn-primary flex items-center gap-2"
                onClick={() => detectMutation.mutate()}
                disabled={
                  detectMutation.isPending ||
                  !!detectJobId ||
                  (detectTask === "<CAPTION_TO_PHRASE_GROUNDING>" && !detectPrompt.trim())
                }

              >
                <ScanSearch size={14} /> {detectJobId ? "Running…" : "Run Detection"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
