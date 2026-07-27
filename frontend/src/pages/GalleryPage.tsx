import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { ArrowRightFromLine, Copy } from "lucide-react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../utils/apiError";
import { createPortal } from "react-dom";
import {
  DndContext, DragOverlay, closestCenter, pointerWithin, PointerSensor, useSensor, useSensors,
  type DragEndEvent, type DragStartEvent, type CollisionDetection,
} from "@dnd-kit/core";
import { SortableContext, rectSortingStrategy, arrayMove } from "@dnd-kit/sortable";
import { imagesApi, type UploadResult } from "../api/images";
import type { ImageListItem, SubfolderInfo } from "../types";
import GenerationMetadata from "../components/image/GenerationMetadata";
import ConfirmDialog from "../components/common/ConfirmDialog";
import MoveToDatasetModal from "../components/common/MoveToDatasetModal";
import ImportFolderModal from "../components/common/ImportFolderModal";
import { datasetsApi } from "../api/datasets";
import { jobsApi } from "../api/jobs";
import { showImportSummaryToast } from "../utils/importToast";
import { showUploadSummaryToast, tallyUpload } from "../utils/uploadToast";
import ImageCard, { SortableImageCard } from "../components/gallery/ImageCard";
import DropZone from "../components/gallery/DropZone";
import SelectionToolbar from "../components/gallery/SelectionToolbar";
import { useSelectionStore } from "../store/selectionStore";
import { useUploadStore } from "../store/uploadStore";
import { useJobStore } from "../store/jobStore";
import { settingsApi } from "../api/settings";
import { getGalleryPageSize, getGalleryDefaultSort, getGalleryDefaultCaptionFilter, getGalleryDefaultQualityFilter, SUBFOLDER_RENAME_KEY } from "../constants/storage";
import { LICENSE_OPTIONS, OTHER_PREFIX, isKnownLicenseValue } from "../constants/licenses";
import { useCustomLicenses } from "../hooks/useCustomLicenses";
import { MISSING_LICENSE, SORT_OPTIONS, isSubfolderDropId, subfolderDropId, subfolderFromDropId, SIDEBAR_DROP_ID } from "../constants/galleryOptions";
import { MEDIA_ACCEPT, isMediaDragItem, isMediaFile } from "../constants/mediaTypes";

type QualityFilter = "" | "is_blurry" | "is_noisy" | "is_uniform" | "has_watermark" | "is_duplicate" | "is_nsfw" | "has_ai_artifacts";

interface ScoreFilter { field: string; min: string; max: string; }

const SCORE_FIELDS = [
  { value: "aesthetic_score",        label: "Aesthetic (1–10)",  short: "Aesthetic"  },
  { value: "watermark_score",        label: "Watermark (0–1)",   short: "Watermark"  },
  { value: "style_similarity_score", label: "Style sim. (0–1)",  short: "Style sim." },
  { value: "blur_score",             label: "Blur",              short: "Blur"       },
  { value: "noise_score",            label: "Noise",             short: "Noise"      },
  { value: "uniformity_score",       label: "Uniformity",        short: "Uniformity" },
  { value: "color_score",            label: "Color",             short: "Color"      },
  { value: "saturation_score",       label: "Saturation",        short: "Saturation" },
];

interface SubfolderNode extends SubfolderInfo {
  label: string;
  depth: number;
  children: SubfolderNode[];
  totalCount: number;
}

function buildSubfolderTree(items: SubfolderInfo[]): SubfolderNode[] {
  const map = new Map<string, SubfolderNode>();
  for (const item of items) {
    if (!item.path) continue;
    map.set(item.path, {
      ...item,
      label: item.path.split("/").pop()!,
      depth: item.path.split("/").length - 1,
      children: [],
      totalCount: item.image_count,
    });
  }
  const roots: SubfolderNode[] = [];
  for (const node of map.values()) {
    const parts = node.path.split("/");
    if (parts.length === 1) {
      roots.push(node);
    } else {
      const parent = map.get(parts.slice(0, -1).join("/"));
      if (parent) parent.children.push(node); else roots.push(node);
    }
  }
  function computeTotals(n: SubfolderNode): void {
    n.children.sort((a, b) => a.label.localeCompare(b.label));
    for (const c of n.children) computeTotals(c);
    n.totalCount = n.image_count + n.children.reduce((s, c) => s + c.totalCount, 0);
  }
  roots.sort((a, b) => a.label.localeCompare(b.label));
  roots.forEach(computeTotals);
  return roots;
}

function scoreChipLabel(f: ScoreFilter): string {
  const short = SCORE_FIELDS.find(s => s.value === f.field)?.short ?? f.field;
  if (f.min && f.max) return `${short}: ${f.min}–${f.max}`;
  if (f.min) return `${short} ≥ ${f.min}`;
  if (f.max) return `${short} ≤ ${f.max}`;
  return short;
}

function loadSavedState(datasetId: string) {
  try {
    const raw = localStorage.getItem(`gallery-state-${datasetId}`);
    if (raw) return JSON.parse(raw) as { page: number; sortIdx: number; captionedFilter: boolean | null; qualityFilter?: string; licenseFilter?: string; scrollTop: number; activeSubfolder?: string | null };
  } catch {}
  return null;
}

export default function GalleryPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const { selectAll, clear, count, toggle, replaceRange, selectedIds, datasetByImageId } = useSelectionStore();

  const pageSize = useMemo(getGalleryPageSize, []);

  const saved = useMemo(() => (datasetId ? loadSavedState(datasetId) : null), [datasetId]);
  const [page, setPage] = useState(saved?.page ?? 1);
  const [sortIdx, setSortIdx] = useState(saved?.sortIdx ?? getGalleryDefaultSort());
  const [captionedFilter, setCaptionedFilter] = useState<boolean | undefined>(
    saved?.captionedFilter == null ? getGalleryDefaultCaptionFilter() : saved.captionedFilter
  );
  // "" = no license filter; "__missing__" = only images with no license at
  // either level; anything else = that effective license id.
  // Bounds-checked against the vocabulary the way getGalleryDefaultSort bounds-checks
  // its index: this comes back from localStorage, possibly written by a build whose
  // vocabulary has since changed, and an unknown id silently filters to zero images
  // with no dropdown option showing why.
  const [licenseFilter, setLicenseFilter] = useState(() => {
    const restored = String(saved?.licenseFilter ?? "");
    if (restored === MISSING_LICENSE || isKnownLicenseValue(restored)) return restored;
    return "";
  });
  // Free-text licenses recorded in this dataset — the vocabulary is compiled in,
  // but an `other:` license exists only in the data, so it can only be offered by
  // asking. A restored filter that is no longer in use is kept in the list too,
  // or the `<select>` would show no option for the filter it is applying.
  const customLicenses = useCustomLicenses(datasetId);
  const licenseFilterOptions = useMemo(() => {
    const isCustom = licenseFilter.toLowerCase().startsWith(OTHER_PREFIX);
    return isCustom && !customLicenses.includes(licenseFilter)
      ? [...customLicenses, licenseFilter]
      : customLicenses;
  }, [customLicenses, licenseFilter]);
  const [qualityFilter, setQualityFilter] = useState<QualityFilter>(
    (saved?.qualityFilter ?? getGalleryDefaultQualityFilter()) as QualityFilter
  );
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [detectionLabelInput, setDetectionLabelInput] = useState("");
  const [detectionLabel, setDetectionLabel] = useState("");
  const [scoreFilters, setScoreFilters] = useState<ScoreFilter[]>([]);
  const [showAddScore, setShowAddScore] = useState(false);
  const [draftField, setDraftField] = useState(SCORE_FIELDS[0].value);
  const [draftMin, setDraftMin] = useState("");
  const [draftMax, setDraftMax] = useState("");
  const { progress: globalUploadProgress, setProgress: setUploadProgress } = useUploadStore();
  // Only treat as "uploading" when the active upload is for this dataset
  const uploadProgress = globalUploadProgress?.datasetId === datasetId ? globalUploadProgress : null;
  const uploading = uploadProgress !== null;
  const [isDragOver, setIsDragOver] = useState(false);
  const dragDepth = useRef(0);
  const [genMetaImage, setGenMetaImage] = useState<ImageListItem | null>(null);
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>(
    saved?.activeSubfolder == null ? undefined : saved.activeSubfolder
  );
  const [uploadSubfolder, setUploadSubfolder] = useState("");
  const [showCreateSubfolder, setShowCreateSubfolder] = useState(false);
  const [newSubfolderName, setNewSubfolderName] = useState("");
  const [pendingDeleteSubfolder, setPendingDeleteSubfolder] = useState<SubfolderInfo | null>(null);
  const [pendingMoveSubfolder, setPendingMoveSubfolder] = useState<SubfolderInfo | null>(null);
  const [pendingCopySubfolder, setPendingCopySubfolder] = useState<SubfolderInfo | null>(null);
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());
  const [createChildOf, setCreateChildOf] = useState<string | null>(null);
  const [newChildName, setNewChildName] = useState("");

  const sortOpt = SORT_OPTIONS[sortIdx];
  const isCustomOrder = sortOpt.sort === "sort_order";
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasRestoredScroll = useRef(false);
  const liveStateRef = useRef({ page, sortIdx, captionedFilter, qualityFilter, licenseFilter, activeSubfolder });
  liveStateRef.current = { page, sortIdx, captionedFilter, qualityFilter, licenseFilter, activeSubfolder };
  const [showRenumberConfirm, setShowRenumberConfirm] = useState(false);
  const prevSortIdxRef = useRef(sortIdx);
  const imagesRef = useRef<ImageListItem[]>([]);
  const lastSelectedId = useRef<string | null>(null);
  const lastRangeEndId = useRef<string | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));
  const [activeDragId, setActiveDragId] = useState<string | null>(null);

  // closestCenter alone is wrong once the 180px sidebar rows are droppables (a card
  // dragged near the grid's left edge can be "closest" to a row), but pointerWithin
  // alone makes reordering feel dead in the gutters between cards. Compose the two.
  const collisionDetection = useCallback<CollisionDetection>((args) => {
    const pointer = pointerWithin(args);
    // A sidebar row under the pointer always wins — rows never overlap cards.
    const folderHit = pointer.find(c => isSubfolderDropId(c.id));
    if (folderHit) return [folderHit];
    // Inside the sidebar but not on a row ("All", the header, the create form, the
    // padding below the last row): swallow the drop. Falling through would hand
    // closestCenter — which scores by the dragged card's rect, not the pointer — a grid
    // card, silently reordering an image the user was only trying to file away.
    if (pointer.some(c => c.id === SIDEBAR_DROP_ID)) return [];
    if (pointer.length > 0) return pointer;
    // Gutters between cards: fall back to the nearest card, never a folder row or the
    // sidebar sentinel (a 180px-wide rect would often out-score a card).
    return closestCenter({
      ...args,
      // Sentinel check first: `isSubfolderDropId` is a `id is string` predicate, so a
      // leading `!isSubfolderDropId(...)` narrows `c.id` to `number` and the sentinel
      // comparison stops compiling.
      droppableContainers: args.droppableContainers.filter(
        c => c.id !== SIDEBAR_DROP_ID && !isSubfolderDropId(c.id)
      ),
    });
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
      hasRestoredScroll.current = false;
    }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    const t = setTimeout(() => {
      setDetectionLabel(detectionLabelInput);
      setPage(1);
      hasRestoredScroll.current = false;
    }, 350);
    return () => clearTimeout(t);
  }, [detectionLabelInput]);

  // Persist gallery state (page/sort/filters) — debounced, survives browser restart.
  useEffect(() => {
    if (!datasetId) return;
    const t = setTimeout(() => {
      const scrollTop = scrollRef.current?.scrollTop ?? 0;
      localStorage.setItem(
        `gallery-state-${datasetId}`,
        JSON.stringify({ page, sortIdx, captionedFilter: captionedFilter ?? null, qualityFilter, licenseFilter, scrollTop, activeSubfolder: activeSubfolder ?? null })
      );
    }, 350);
    return () => clearTimeout(t);
  }, [datasetId, page, sortIdx, captionedFilter, qualityFilter, licenseFilter, activeSubfolder]);

  // Save precise scroll position + current state on unmount via ref — avoids stale localStorage reads
  // and the debounce gap where a <350ms navigation would otherwise lose state changes.
  useEffect(() => {
    return () => {
      if (!datasetId) return;
      const scrollTop = scrollRef.current?.scrollTop ?? 0;
      const { page, sortIdx, captionedFilter, qualityFilter, licenseFilter, activeSubfolder } = liveStateRef.current;
      try {
        localStorage.setItem(
          `gallery-state-${datasetId}`,
          JSON.stringify({ page, sortIdx, captionedFilter: captionedFilter ?? null, qualityFilter, licenseFilter, scrollTop, activeSubfolder: activeSubfolder ?? null })
        );
      } catch {}
    };
  }, [datasetId]); // eslint-disable-line react-hooks/exhaustive-deps

  const { data: dataset } = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => datasetsApi.get(datasetId!),
    enabled: !!datasetId,
  });

  // Full dataset list so the import modal lets the user retarget without leaving the gallery.
  const { data: allDatasets = [] } = useQuery({
    queryKey: ["datasets"],
    queryFn: datasetsApi.list,
    staleTime: 30_000,
  });

  const { data: subfolders = [] } = useQuery<SubfolderInfo[]>({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  const subfolderTree = useMemo(() => buildSubfolderTree(subfolders), [subfolders]);

  useEffect(() => {
    setUploadSubfolder(activeSubfolder ?? "");
  }, [activeSubfolder]);

  // ── Rescan from disk (manual button + auto-on-open gated by Settings) ──────
  const { data: thresholds } = useQuery({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });
  const [rescanJobId, setRescanJobId] = useState<string | null>(null);
  const rescanProgress = useJobStore((s) => s.activeJobs.get(rescanJobId ?? ""));
  const autoRescannedRef = useRef<Set<string>>(new Set());
  // true when the active rescan was started by the user (show a summary toast on completion)
  const rescanManualRef = useRef(false);

  const runRescan = useCallback((manual: boolean) => {
    if (!datasetId || rescanJobId) return;
    rescanManualRef.current = manual;
    if (manual) toast.loading("Rescanning folder…", { id: "gallery-rescan" });
    datasetsApi.rescan(datasetId, true)
      .then((data) => setRescanJobId(data.job_id))
      .catch(() => { if (manual) toast.error("Rescan failed", { id: "gallery-rescan" }); });
  }, [datasetId, rescanJobId]);

  useEffect(() => {
    if (!datasetId || !thresholds?.auto_rescan_on_open) return;
    if (autoRescannedRef.current.has(datasetId)) return;
    autoRescannedRef.current.add(datasetId);
    runRescan(false);
  }, [datasetId, thresholds?.auto_rescan_on_open, runRescan]);

  useEffect(() => {
    if (!rescanJobId || rescanProgress?.status !== "completed") return;
    qc.invalidateQueries({ queryKey: ["images", datasetId] });
    qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
    qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
    qc.invalidateQueries({ queryKey: ["datasets"] });
    qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
    qc.invalidateQueries({ queryKey: ["tag-stats", datasetId] });
    qc.invalidateQueries({ queryKey: ["score-values", datasetId] });
    qc.invalidateQueries({ queryKey: ["tag-cooccurrence", datasetId] });
    if (rescanManualRef.current) {
      const jid = rescanJobId;
      jobsApi.get(jid).then((job) => {
        const r = job.result_data as {
          added?: number; captions_updated?: number; missing?: unknown[];
          videos_added?: number; videos_missing?: unknown[];
        };
        const missing = (r.missing ?? []).length + (r.videos_missing ?? []).length;
        toast.success(
          `Rescan complete — ${r.added ?? 0} added, ${r.captions_updated ?? 0} caption(s) updated` +
          (r.videos_added ? `, ${r.videos_added} video(s) added` : "") +
          (missing ? `, ${missing} missing on disk` : ""),
          { id: "gallery-rescan" }
        );
      }).catch(() => toast.success("Rescan complete", { id: "gallery-rescan" }));
    }
    setRescanJobId(null);
  }, [rescanProgress?.status, rescanJobId, datasetId, qc]);

  // ── Import folder (from the gallery toolbar) ──────────────────────────────
  const [showImport, setShowImport] = useState(false);
  const [importJobId, setImportJobId] = useState<string | null>(null);
  const importProgress = useJobStore((s) => s.activeJobs.get(importJobId ?? ""));

  useEffect(() => {
    if (!importJobId || importProgress?.status !== "completed") return;
    qc.invalidateQueries({ queryKey: ["images", datasetId] });
    qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
    qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
    qc.invalidateQueries({ queryKey: ["datasets"] });
    qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
    qc.invalidateQueries({ queryKey: ["tag-stats", datasetId] });
    qc.invalidateQueries({ queryKey: ["score-values", datasetId] });
    qc.invalidateQueries({ queryKey: ["tag-cooccurrence", datasetId] });
    // An import is a provenance writer: a scraper sidecar is the largest source
    // of new `other:` licenses, and they are unpickable until this refetches.
    qc.invalidateQueries({ queryKey: ["licenses-in-use", datasetId] });
    showImportSummaryToast(importJobId);
    setImportJobId(null);
  }, [importProgress?.status, importJobId, datasetId, qc]);

  const scoreFiltersParam = scoreFilters.length > 0
    ? JSON.stringify(scoreFilters.map(f => ({
        field: f.field,
        min: f.min !== "" ? parseFloat(f.min) : undefined,
        max: f.max !== "" ? parseFloat(f.max) : undefined,
      })))
    : undefined;

  const imagesQueryKey = useMemo(
    () => ["images", datasetId, page, pageSize, sortOpt, captionedFilter, qualityFilter, search, scoreFiltersParam, activeSubfolder, detectionLabel, licenseFilter],
    [datasetId, page, pageSize, sortOpt, captionedFilter, qualityFilter, search, scoreFiltersParam, activeSubfolder, detectionLabel, licenseFilter]
  );

  const { data: images = [], isLoading, refetch } = useQuery({
    queryKey: imagesQueryKey,
    queryFn: () =>
      imagesApi.list({
        dataset_id: datasetId!,
        page,
        limit: pageSize,
        sort: sortOpt.sort,
        order: sortOpt.order,
        captioned: captionedFilter,
        search: search || undefined,
        quality_flag: qualityFilter || undefined,
        score_filters: scoreFiltersParam,
        subfolder: activeSubfolder,
        detection_label: detectionLabel || undefined,
        license_missing: licenseFilter === MISSING_LICENSE ? true : undefined,
        license_filter:
          licenseFilter && licenseFilter !== MISSING_LICENSE
            ? JSON.stringify([licenseFilter])
            : undefined,
      }),
    enabled: !!datasetId,
    placeholderData: keepPreviousData,
  });

  imagesRef.current = images;

  const handleSelect = useCallback((id: string, shiftKey: boolean, isCheckbox: boolean) => {
    if (shiftKey && isCheckbox && lastSelectedId.current !== null) {
      const ids = images.map(i => i.id);
      const a = ids.indexOf(lastSelectedId.current);
      const b = ids.indexOf(id);
      if (a !== -1 && b !== -1) {
        const newRange = ids.slice(Math.min(a, b), Math.max(a, b) + 1);
        let toRemove: string[] = [];
        if (lastRangeEndId.current !== null) {
          const prevB = ids.indexOf(lastRangeEndId.current);
          if (prevB !== -1) {
            const oldRange = ids.slice(Math.min(a, prevB), Math.max(a, prevB) + 1);
            const newRangeSet = new Set(newRange);
            toRemove = oldRange.filter(rid => !newRangeSet.has(rid));
          }
        }
        replaceRange(newRange, toRemove, datasetId ?? "");
        lastRangeEndId.current = id;
        return; // anchor stays unchanged
      }
    }
    toggle(id, datasetId ?? "");
    // Update anchor only for non-shift clicks, or shift+checkbox when range couldn't apply.
    // Shift+card-body (!isCheckbox) is a plain toggle that must not move the anchor.
    if (!shiftKey || isCheckbox) {
      lastSelectedId.current = id;
      lastRangeEndId.current = null;
    }
  }, [images, datasetId, toggle, replaceRange]);

  useEffect(() => {
    if (!isLoading && images.length > 0 && !hasRestoredScroll.current && scrollRef.current && saved?.scrollTop) {
      hasRestoredScroll.current = true;
      scrollRef.current.scrollTop = saved.scrollTop;
    }
  }, [isLoading, images.length, saved]);

  useEffect(() => {
    if (images.length > 0 && datasetId) {
      sessionStorage.setItem(
        `gallery-nav-${datasetId}`,
        JSON.stringify({ ids: images.map((i) => i.id), page, sort: sortOpt.sort, order: sortOpt.order, captionedFilter: captionedFilter ?? null })
      );
    }
  }, [images, datasetId, page, sortOpt, captionedFilter]);

  const reorderMutation = useMutation({
    mutationFn: (updates: { id: string; sort_order: number }[]) =>
      imagesApi.reorderImages(datasetId!, updates),
    onError: () => toast.error("Failed to save order"),
  });

  // Mirrors SelectionToolbar's moveSubfolderMutation so the drag path and the toolbar
  // path can't diverge — same rename preference, same invalidations, same toast.
  const moveToSubfolderMutation = useMutation({
    mutationFn: (p: { ids: string[]; target: string }) =>
      imagesApi.batchMoveSubfolder(p.ids, p.target, localStorage.getItem(SUBFOLDER_RENAME_KEY) !== "off"),
    onSuccess: (data, vars) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      // Only for multi-moves — dragging a single unselected card must not wipe
      // an unrelated selection.
      if (vars.ids.length > 1) clear();
      toast.success(`Moved ${data.moved} image${data.moved !== 1 ? "s" : ""} to "${data.subfolder || "(root)"}"`);
    },
    onError: (err) => {
      toast.error(apiErrorDetail(err, "Move failed"));
    },
  });

  // Images to move for a drag starting on `draggedId`: the whole selection if the
  // dragged card is part of it, otherwise just that card.
  const dragIdsFor = useCallback((draggedId: string) => {
    // The selection store is module-global, so in a split-pane setup it can hold ids
    // from another dataset — the backend derives dataset_id from the first row and
    // would silently move them into this dataset's folder.
    return selectedIds.has(draggedId)
      ? [...selectedIds].filter(id => datasetByImageId.get(id) === datasetId)
      : [draggedId];
  }, [selectedIds, datasetByImageId, datasetId]);

  const activeDragImage = useMemo(
    () => (activeDragId ? images.find(i => i.id === activeDragId) ?? null : null),
    [images, activeDragId]
  );
  const activeDragCount = activeDragId ? dragIdsFor(activeDragId).length : 0;

  const moveImagesTo = useCallback((draggedId: string, target: string) => {
    if (!datasetId) return;
    const ids = dragIdsFor(draggedId);
    if (!ids.length) return;

    // The backend does not filter out images already in the target, and with
    // rename_on_move they'd be pointlessly renamed to a fresh unique stem.
    const known = new Map(images.map(i => [i.id, i.subfolder]));
    const toMove = ids.filter(id => (known.get(id) ?? null) !== target);
    if (!toMove.length) {
      toast(`Already in "${target || "(root)"}"`);
      return;
    }
    moveToSubfolderMutation.mutate({ ids: toMove, target });
  }, [datasetId, dragIdsFor, images, moveToSubfolderMutation]);

  const renumberMutation = useMutation({
    mutationFn: () => {
      const stem = activeSubfolder ? (activeSubfolder.split("/").pop() || "image") : "image";
      return imagesApi.bulkRename(datasetId!, {
        newStem: stem,
        subfolder: activeSubfolder,
        sortBySortOrder: true,
      });
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      toast.success(`Renamed ${data.affected} file${data.affected !== 1 ? "s" : ""}`);
      setShowRenumberConfirm(false);
    },
    onError: () => toast.error("Rename failed"),
  });

  // When switching TO custom order for the first time, initialize sort_order from current page order.
  useEffect(() => {
    const wasCustom = SORT_OPTIONS[prevSortIdxRef.current]?.sort === "sort_order";
    prevSortIdxRef.current = sortIdx;
    const nowCustom = SORT_OPTIONS[sortIdx]?.sort === "sort_order";
    if (!wasCustom && nowCustom && datasetId) {
      const current = imagesRef.current;
      if (current.length === 0) return;
      const anyHasSortOrder = current.some(img => img.sort_order != null);
      if (!anyHasSortOrder) {
        const pageOffset = (page - 1) * pageSize;
        imagesApi.reorderImages(datasetId, current.map((img, idx) => ({ id: img.id, sort_order: pageOffset + idx })))
          .then(() => qc.invalidateQueries({ queryKey: ["images", datasetId] }))
          .then(() => toast.success("Custom order initialized"))
          .catch(() => toast.error("Failed to initialize order"));
      }
    }
  }, [sortIdx, datasetId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDragStart = useCallback((e: DragStartEvent) => setActiveDragId(String(e.active.id)), []);
  const handleDragCancel = useCallback(() => setActiveDragId(null), []);

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event;
    setActiveDragId(null);
    if (!over || !datasetId) return;

    // Dropped on a sidebar subfolder row → move rather than reorder.
    if (isSubfolderDropId(over.id)) {
      moveImagesTo(String(active.id), subfolderFromDropId(over.id));
      return;
    }

    // Reorder — only meaningful in custom-order mode.
    if (!isCustomOrder || active.id === over.id) return;
    const cached = qc.getQueryData<ImageListItem[]>(imagesQueryKey) ?? [];
    const oldIndex = cached.findIndex(img => img.id === active.id);
    const newIndex = cached.findIndex(img => img.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    const newOrder = arrayMove(cached, oldIndex, newIndex);
    qc.setQueryData(imagesQueryKey, newOrder);
    const pageOffset = (page - 1) * pageSize;
    reorderMutation.mutate(newOrder.map((img, idx) => ({ id: img.id, sort_order: pageOffset + idx })));
  }, [qc, imagesQueryKey, datasetId, page, pageSize, reorderMutation, isCustomOrder, moveImagesTo]);

  const handleUpload = useCallback(async (files: FileList | File[], sf?: string) => {
    if (!datasetId) return;
    const fileArray = Array.from(files);
    const subfolder = sf ?? uploadSubfolder;
    setUploadProgress({ datasetId, done: 0, total: fileArray.length, errors: 0 });
    let errors = 0;
    // A file the server declined comes back 201 with a `skipped` entry, not an
    // exception, so the responses are collected and tallied rather than assuming
    // every file that did not throw was stored.
    const results: UploadResult[] = [];
    let skippedSoFar = 0;
    for (let i = 0; i < fileArray.length; i++) {
      try {
        const res = await imagesApi.uploadSingle(datasetId, fileArray[i], subfolder);
        results.push(res);
        skippedSoFar += res.skipped.length;
        // Invalidate after each success so images appear in the gallery live.
        // cancelRefetch: false lets in-flight fetches finish instead of being
        // restarted on every file, coalescing rapid invalidations into fewer GETs.
        qc.invalidateQueries({ queryKey: ["images", datasetId] }, { cancelRefetch: false });
      } catch {
        errors++;
      }
      setUploadProgress({ datasetId, done: i + 1, total: fileArray.length, errors: errors + skippedSoFar });
    }
    // Final refresh to ensure the gallery is fully up-to-date
    await refetch();
    qc.invalidateQueries({ queryKey: ["datasets"] });
    qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
    qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
    qc.invalidateQueries({ queryKey: ["videos", datasetId] });
    showUploadSummaryToast(tallyUpload(results, errors));
    setUploadProgress(null);
  }, [datasetId, refetch, qc, uploadSubfolder]); // setUploadProgress omitted — Zustand setters are stable references

  const createSubfolderMutation = useMutation({
    mutationFn: (path: string) => datasetsApi.createSubfolder(datasetId!, path),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      setShowCreateSubfolder(false);
      setNewSubfolderName("");
      setCreateChildOf(null);
      setNewChildName("");
      // Auto-expand all ancestor paths so the new subfolder is visible
      const parts = data.path.split("/");
      if (parts.length > 1) {
        setExpandedPaths(prev => {
          const next = new Set(prev);
          for (let i = 1; i < parts.length; i++) next.add(parts.slice(0, i).join("/"));
          return next;
        });
      }
      setActiveSubfolder(data.path);
      toast.success(`Created subfolder "${data.path}"`);
    },
    onError: () => toast.error("Failed to create subfolder"),
  });

  const deleteSubfolderMutation = useMutation({
    mutationFn: (path: string) => datasetsApi.deleteSubfolder(datasetId!, path),
    onSuccess: (_data, path) => {
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setPendingDeleteSubfolder(null);
      if (activeSubfolder === path || activeSubfolder?.startsWith(path + "/")) {
        setActiveSubfolder(undefined);
        resetPage();
      }
      toast.success(`Deleted subfolder "${path}"`);
    },
    onError: () => toast.error("Failed to delete subfolder"),
  });

  const moveSubfolderToDatasetMutation = useMutation({
    mutationFn: (params: { targetId: string; subfolder: string }) =>
      imagesApi.batchMoveDataset(
        { source_dataset_id: datasetId!, source_subfolder: pendingMoveSubfolder!.path },
        params.targetId,
        params.subfolder,
      ),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["subfolders", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset", data.target_dataset_id] });
      if (activeSubfolder === pendingMoveSubfolder?.path || activeSubfolder?.startsWith(pendingMoveSubfolder!.path + "/")) { setActiveSubfolder(undefined); resetPage(); }
      toast.success(`Moved ${data.moved} image${data.moved !== 1 ? "s" : ""} to dataset`);
      setPendingMoveSubfolder(null);
    },
    onError: () => toast.error("Move to dataset failed"),
  });

  const copySubfolderToDatasetMutation = useMutation({
    mutationFn: (params: { targetId: string; subfolder: string }) =>
      imagesApi.batchCopyDataset(
        { source_dataset_id: datasetId!, source_subfolder: pendingCopySubfolder!.path },
        params.targetId,
        params.subfolder,
      ),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["subfolders", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["dataset", data.target_dataset_id] });
      toast.success(`Copied ${data.copied} image${data.copied !== 1 ? "s" : ""} to dataset`);
      setPendingCopySubfolder(null);
    },
    onError: () => toast.error("Copy to dataset failed"),
  });

  // Only the upload overlay cares about *media* drags. A .txt caption drag is
  // consumed per-card (which stopPropagations, so the grid's drop never fires) — gating
  // the overlay on media presence means a caption drag never turns it on, so it can't
  // get stuck. The depth counter keeps nested child enter/leave events balanced.
  const dragHasMedia = (dt: DataTransfer | null) =>
    !!dt && Array.from(dt.items || []).some(isMediaDragItem);

  const handleDragEnter = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    dragDepth.current += 1;
    if (dragHasMedia(e.dataTransfer)) {
      e.preventDefault();
      setIsDragOver(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault(); // always, so the browser never opens/navigates to a dropped file
    dragDepth.current = 0;
    setIsDragOver(false);
    // Only upload images and videos; a stray .txt dropped on the grid gap is ignored here
    // (per-card caption drops are handled by ImageCard and never reach this handler).
    // isMediaFile falls back to the extension because browsers report an empty `type` for
    // .mkv and often .avi — a MIME-only filter would drop them silently.
    const mediaFiles = Array.from(e.dataTransfer.files).filter(isMediaFile);
    if (!uploading && mediaFiles.length) handleUpload(mediaFiles);
  };

  // Safety net: if a drag ends anywhere (drop outside the grid, Esc-cancel, or a child
  // that consumed the drop), force the overlay off so it can never persist. Registered in
  // the CAPTURE phase: a per-card caption drop calls stopPropagation() (to keep the .txt
  // from reaching the image uploader), which would otherwise block a bubble-phase window
  // listener and leave dragDepth stuck non-zero — desyncing every later drag's overlay.
  // Capture runs window→target before any descendant stopPropagation, so it always fires.
  useEffect(() => {
    const reset = () => { dragDepth.current = 0; setIsDragOver(false); };
    window.addEventListener("drop", reset, true);
    window.addEventListener("dragend", reset, true);
    return () => {
      window.removeEventListener("drop", reset, true);
      window.removeEventListener("dragend", reset, true);
    };
  }, []);

  const flaggedCount = dataset ? (dataset.image_count - dataset.captioned_count) : 0; // placeholder

  const resetPage = () => { setPage(1); hasRestoredScroll.current = false; };

  const handleResetFilters = () => {
    if (datasetId) localStorage.removeItem(`gallery-state-${datasetId}`);
    setPage(1);
    setSortIdx(getGalleryDefaultSort());
    setCaptionedFilter(getGalleryDefaultCaptionFilter());
    setQualityFilter(getGalleryDefaultQualityFilter() as QualityFilter);
    setLicenseFilter("");
    setActiveSubfolder(undefined);
    hasRestoredScroll.current = true;
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
    toast.success("Gallery filters reset");
  };

  const applyScoreFilter = () => {
    if (!draftMin && !draftMax) return;
    setScoreFilters(prev => [...prev, { field: draftField, min: draftMin, max: draftMax }]);
    setDraftMin("");
    setDraftMax("");
    setShowAddScore(false);
    resetPage();
  };

  // Always offer a (root) drop target. list_subfolders only returns a "" row while at
  // least one image still lives there, so dragging the last one out would otherwise
  // remove the only way to drag images back to root.
  const rootEntry = subfolders.find(sf => sf.path === "") ?? { path: "", image_count: 0 };
  const isRootActive = activeSubfolder === "";

  const deleteDialogMessage = pendingDeleteSubfolder
    ? (() => {
        const childFolders = pendingDeleteSubfolder.path
          ? subfolders.filter(sf => sf.path.startsWith(pendingDeleteSubfolder.path + "/"))
          : [];
        const childCount = childFolders.length;
        const totalImages = pendingDeleteSubfolder.image_count + childFolders.reduce((s, sf) => s + sf.image_count, 0);
        const parts: string[] = [];
        if (childCount > 0) parts.push(`${childCount} child subfolder${childCount !== 1 ? "s" : ""}`);
        if (totalImages > 0) parts.push(`${totalImages} image${totalImages !== 1 ? "s" : ""}`);
        return parts.length > 0
          ? `This will remove ${parts.join(" and ")}. All images will be moved to root (ungrouped) — not deleted.`
          : "This empty subfolder will be removed.";
      })()
    : "";

  const renderSubfolderNode = (node: SubfolderNode) => {
    const isExpanded = expandedPaths.has(node.path);
    const isActive = activeSubfolder === node.path;
    const hasChildren = node.children.length > 0;

    return (
      <div key={node.path}>
        <DropZone id={subfolderDropId(node.path)}>
        {({ setNodeRef, isOver }) => (
        <div
          ref={setNodeRef}
          className="subfolder-row"
          style={{
            display: "flex", alignItems: "center",
            borderRadius: "var(--r)",
            background: isOver || isActive ? "var(--surface-3)" : "transparent",
            boxShadow: isOver ? "inset 0 0 0 1px var(--accent)" : "none",
          }}
        >
          {/* expand/collapse toggle (doubles as indent) */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (!hasChildren) return;
              setExpandedPaths(prev => {
                const next = new Set(prev);
                if (next.has(node.path)) next.delete(node.path); else next.add(node.path);
                return next;
              });
            }}
            style={{
              flexShrink: 0,
              width: 8 + node.depth * 12 + 12,
              minHeight: 28, border: "none",
              background: "transparent",
              cursor: hasChildren ? "pointer" : "default",
              color: "var(--fg-mute)", fontSize: 7,
              paddingLeft: 8 + node.depth * 12,
              display: "flex", alignItems: "center", justifyContent: "flex-end", paddingRight: 2,
            }}
          >{hasChildren ? (isExpanded ? "▼" : "▶") : ""}</button>

          {/* label + count */}
          <button
            onClick={() => { setActiveSubfolder(node.path); resetPage(); }}
            title={node.path}
            style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              flex: 1, minWidth: 0, padding: "5px 4px",
              border: "none", cursor: "pointer", textAlign: "left",
              background: "transparent",
              color: isActive ? "var(--accent)" : "var(--fg)",
              fontSize: 12.5, fontWeight: isActive ? 600 : 400,
            }}
          >
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{node.label}</span>
            <span style={{ fontSize: 11, color: "var(--fg-mute)", marginLeft: 4, flexShrink: 0 }}>{node.totalCount || ""}</span>
          </button>

          {/* hover actions */}
          <button
            className="subfolder-add-child-btn"
            title={`Add subfolder inside "${node.path}"`}
            onClick={(e) => {
              e.stopPropagation();
              setCreateChildOf(createChildOf === node.path ? null : node.path);
              setNewChildName("");
            }}
          >+</button>
          <button
            className="subfolder-action-btn subfolder-move-btn"
            title={`Move "${node.path}" to another dataset`}
            onClick={(e) => { e.stopPropagation(); setPendingMoveSubfolder(node); }}
          ><ArrowRightFromLine size={11} /></button>
          <button
            className="subfolder-action-btn subfolder-copy-btn"
            title={`Copy "${node.path}" to another dataset`}
            onClick={(e) => { e.stopPropagation(); setPendingCopySubfolder(node); }}
          ><Copy size={11} /></button>
          <button
            className="subfolder-delete-btn"
            title={`Delete "${node.path}"`}
            onClick={(e) => { e.stopPropagation(); setPendingDeleteSubfolder(node); }}
            style={{
              flexShrink: 0, width: 18, height: 18, padding: 0,
              border: "none", cursor: "pointer", borderRadius: "var(--r)",
              background: "transparent", color: "var(--fg-mute)",
              fontSize: 13, lineHeight: 1, display: "flex", alignItems: "center", justifyContent: "center",
              marginRight: 4,
            }}
          >×</button>
        </div>
        )}
        </DropZone>

        {/* inline child-create form */}
        {createChildOf === node.path && (
          <div style={{ padding: `2px 6px 6px ${8 + (node.depth + 1) * 12 + 12}px` }}>
            <div style={{ fontSize: 10, color: "var(--fg-mute)", marginBottom: 2 }}>{node.path}/</div>
            <input
              className="input"
              style={{ width: "100%", fontSize: 12, padding: "3px 6px" }}
              placeholder="child-name"
              value={newChildName}
              onChange={(e) => setNewChildName(e.target.value)}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter" && newChildName.trim())
                  createSubfolderMutation.mutate(node.path + "/" + newChildName.trim());
                if (e.key === "Escape") { setCreateChildOf(null); setNewChildName(""); }
              }}
            />
            <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
              <button
                className="btn sm"
                style={{ flex: 1, fontSize: 11 }}
                disabled={!newChildName.trim() || createSubfolderMutation.isPending}
                onClick={() => createSubfolderMutation.mutate(node.path + "/" + newChildName.trim())}
              >Create</button>
              <button
                className="btn ghost sm"
                style={{ fontSize: 11 }}
                onClick={() => { setCreateChildOf(null); setNewChildName(""); }}
              >✕</button>
            </div>
          </div>
        )}

        {/* children */}
        {hasChildren && isExpanded && node.children.map(renderSubfolderNode)}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Page header */}
      <div style={{ padding: "18px 28px 0", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, marginBottom: 0 }}>
          <div>
            <h1 style={{ margin: "0 0 4px", fontSize: 22, fontWeight: 600, letterSpacing: "-.02em" }}>{dataset?.name ?? "Gallery"}</h1>
            <p style={{ margin: 0, color: "var(--fg-mute)", fontSize: 13 }}>
              {dataset?.description && <>{dataset.description} · </>}
              {dataset?.image_count ?? 0} images
            </p>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0, paddingTop: 4 }}>
            <span className="badge dot good">{dataset?.image_count ?? 0} images</span>
            <span className="badge dot info">{dataset?.captioned_count ?? 0} captioned</span>
            {flaggedCount > 0 && <span className="badge dot warn">{flaggedCount} uncaptioned</span>}
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div style={{
        padding: "14px 28px", borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        background: "var(--surface-1)", flexShrink: 0,
      }}>
        <div className="search-wrap">
          <svg className="search-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/>
          </svg>
          <input
            className="input"
            placeholder="Search filename or caption…"
            style={{ width: 280 }}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>

        <select className="select" style={{ width: "auto" }} value={sortIdx}
          onChange={(e) => { setSortIdx(Number(e.target.value)); resetPage(); }}>
          {SORT_OPTIONS.map((o, i) => <option key={i} value={i}>{o.label}</option>)}
        </select>

        <select className="select" style={{ width: "auto" }}
          value={captionedFilter === undefined ? "" : String(captionedFilter)}
          onChange={(e) => { const v = e.target.value; setCaptionedFilter(v === "" ? undefined : v === "true"); resetPage(); }}>
          <option value="">All images</option>
          <option value="true">Captioned only</option>
          <option value="false">Uncaptioned</option>
        </select>

        <select className="select" style={{ width: "auto" }} value={qualityFilter}
          onChange={(e) => { setQualityFilter(e.target.value as QualityFilter); resetPage(); }}>
          <option value="">All quality</option>
          <option value="is_blurry">Flagged: blurry</option>
          <option value="is_noisy">Flagged: noisy</option>
          <option value="is_uniform">Flagged: near-uniform</option>
          <option value="has_watermark">Flagged: watermark</option>
          <option value="is_duplicate">Flagged: duplicate</option>
          <option value="is_nsfw">Flagged: NSFW</option>
          <option value="has_ai_artifacts">Flagged: AI artifacts</option>
        </select>

        <select className="select" style={{ width: "auto" }} value={licenseFilter}
          aria-label="Filter by license"
          onChange={(e) => { setLicenseFilter(e.target.value); resetPage(); }}>
          <option value="">All licenses</option>
          <option value={MISSING_LICENSE}>Missing license only</option>
          {LICENSE_OPTIONS.map((l) => (
            <option key={l.id} value={l.id}>{l.label}</option>
          ))}
          {licenseFilterOptions.length > 0 && (
            <optgroup label="Used in this dataset">
              {licenseFilterOptions.map((lic) => (
                <option key={lic} value={lic}>{lic.slice(OTHER_PREFIX.length)}</option>
              ))}
            </optgroup>
          )}
        </select>

        <button
          className="btn ghost sm"
          onClick={handleResetFilters}
          title="Clear remembered sort/filter settings for this dataset and revert to defaults"
        >
          Reset filters
        </button>

        <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
            style={{ position: "absolute", left: 8, width: 13, height: 13, color: "var(--fg-mute)", pointerEvents: "none" }}>
            <rect x="2" y="3" width="12" height="10" rx="1.5"/>
            <circle cx="8" cy="8" r="2.5"/>
          </svg>
          <input
            className="input"
            placeholder="Objects: cat, dog…"
            style={{ paddingLeft: 26, width: 160 }}
            value={detectionLabelInput}
            onChange={(e) => setDetectionLabelInput(e.target.value)}
            title="Filter by detected object label"
          />
          {detectionLabelInput && (
            <button
              onClick={() => { setDetectionLabelInput(""); setDetectionLabel(""); resetPage(); }}
              style={{ position: "absolute", right: 6, background: "none", border: "none", cursor: "pointer", color: "var(--fg-mute)", fontSize: 14, lineHeight: 1, padding: 0 }}
              title="Clear"
            >×</button>
          )}
        </div>

        {/* Multi-score filters */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {scoreFilters.map((f, i) => (
            <span
              key={i}
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "2px 6px 2px 8px", borderRadius: "var(--r)",
                background: "var(--surface-3)", border: "1px solid var(--accent)",
                fontSize: 12, color: "var(--fg)", whiteSpace: "nowrap",
              }}
            >
              {scoreChipLabel(f)}
              <button
                onClick={() => { setScoreFilters(prev => prev.filter((_, j) => j !== i)); resetPage(); }}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--fg-mute)", padding: "0 1px", fontSize: 14, lineHeight: 1, display: "flex", alignItems: "center" }}
                title="Remove filter"
              >×</button>
            </span>
          ))}

          {showAddScore ? (
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <select
                className="select"
                style={{ width: "auto" }}
                value={draftField}
                onChange={e => setDraftField(e.target.value)}
              >
                {SCORE_FIELDS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
              </select>
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>≥</span>
              <input
                className="input"
                type="number"
                placeholder="min"
                value={draftMin}
                onChange={e => setDraftMin(e.target.value)}
                style={{ width: 62 }}
                onKeyDown={e => {
                  if (e.key === "Enter") applyScoreFilter();
                  if (e.key === "Escape") { setShowAddScore(false); setDraftMin(""); setDraftMax(""); }
                }}
              />
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>≤</span>
              <input
                className="input"
                type="number"
                placeholder="max"
                value={draftMax}
                onChange={e => setDraftMax(e.target.value)}
                style={{ width: 62 }}
                onKeyDown={e => {
                  if (e.key === "Enter") applyScoreFilter();
                  if (e.key === "Escape") { setShowAddScore(false); setDraftMin(""); setDraftMax(""); }
                }}
              />
              <button className="btn sm" onClick={applyScoreFilter} disabled={!draftMin && !draftMax}>Apply</button>
              <button className="icon-btn" style={{ fontSize: 14 }} onClick={() => { setShowAddScore(false); setDraftMin(""); setDraftMax(""); }}>×</button>
            </div>
          ) : (
            <button
              className="btn ghost sm"
              onClick={() => setShowAddScore(true)}
              style={{ whiteSpace: "nowrap" }}
            >
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M8 2v12M2 8h12"/>
              </svg>
              Score filter
            </button>
          )}
        </div>

        <div style={{ flex: 1 }} />

        {subfolders.length === 0 && !showCreateSubfolder && (
          <button
            className="btn ghost sm"
            onClick={() => { setShowCreateSubfolder(true); setNewSubfolderName(""); }}
            style={{ whiteSpace: "nowrap" }}
          >
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M8 2v12M2 8h12"/>
            </svg>
            Subfolder
          </button>
        )}

        <button
          className="btn ghost sm"
          onClick={() => count === images.length ? clear() : selectAll(images.map(i => i.id), datasetId ?? "")}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/>
          </svg>
          {count === images.length && images.length > 0 ? "Deselect all" : "Select all"}
        </button>

        {isCustomOrder && (
          <button
            className="btn ghost sm"
            onClick={() => setShowRenumberConfirm(true)}
            title="Rename files sequentially in current custom order"
            style={{ whiteSpace: "nowrap" }}
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M3 4h2M3 8h4M3 12h6M9 2v4l2-2M13 10a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zM13 10v4"/>
            </svg>
            Renumber
          </button>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {subfolders.length > 0 && (
            <select
              className="select"
              style={{ width: "auto", maxWidth: 120 }}
              value={uploadSubfolder}
              onChange={(e) => setUploadSubfolder(e.target.value)}
              title="Upload to subfolder"
            >
              <option value="">(root)</option>
              {subfolders.filter(sf => sf.path !== "").map(sf => (
                <option key={sf.path} value={sf.path}>{sf.path}</option>
              ))}
            </select>
          )}
          <button
            className="btn ghost"
            title="Import a folder of images into this dataset"
            onClick={() => setShowImport(true)}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M2.5 3.5h4l1.5 2h5.5v7h-11v-9z"/>
            </svg>
            Import folder
          </button>
          <button
            className="btn ghost"
            title="Rescan folder from disk — pick up images and .txt captions added outside the app"
            disabled={rescanJobId !== null}
            onClick={() => runRescan(true)}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2v3h-3"/>
            </svg>
            {rescanJobId !== null ? "Rescanning…" : "Rescan"}
          </button>
          <label className="btn" style={{ cursor: uploading ? "default" : "pointer", opacity: uploading ? 0.65 : 1, pointerEvents: uploading ? "none" : "auto" }}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M8 10V2M5 5l3-3 3 3M2.5 13.5h11"/>
            </svg>
            {uploading ? "Uploading…" : "Upload"}
            <input type="file" multiple accept={MEDIA_ACCEPT} style={{ display: "none" }}
              onChange={(e) => e.target.files && handleUpload(e.target.files)} />
          </label>
        </div>
      </div>

      {/* Upload progress bar */}
      {uploadProgress && (
        <div style={{
          padding: "6px 28px", borderBottom: "1px solid var(--line)",
          background: "var(--surface-1)", flexShrink: 0,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{
              flex: 1, height: 4, background: "var(--surface-3)",
              borderRadius: 2, overflow: "hidden",
            }}>
              <div style={{
                height: "100%",
                width: `${Math.round((uploadProgress.done / uploadProgress.total) * 100)}%`,
                background: uploadProgress.errors > 0 ? "var(--warn)" : "var(--accent)",
                borderRadius: 2,
                transition: "width 0.15s ease",
              }} />
            </div>
            <span style={{
              fontSize: 12, color: "var(--fg-mute)",
              whiteSpace: "nowrap", minWidth: 90, textAlign: "right",
            }}>
              {uploadProgress.done} / {uploadProgress.total} images
              {uploadProgress.errors > 0 && ` · ${uploadProgress.errors} failed`}
            </span>
          </div>
        </div>
      )}

      {/* Grid area with subfolder sidebar. One DndContext spans both so image cards
          can be dragged from the grid onto a subfolder row. */}
      <DndContext
        sensors={sensors}
        collisionDetection={collisionDetection}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragCancel={handleDragCancel}
      >
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* Subfolder sidebar. Itself a sentinel droppable — not a move target, but it
            lets collisionDetection tell "missed a row" from "over the grid" and swallow
            the drop instead of falling through to a reorder. */}
        {(subfolders.length > 0 || showCreateSubfolder) && (
          <DropZone id={SIDEBAR_DROP_ID}>
          {({ setNodeRef }) => (
          <div
            ref={setNodeRef}
            style={{
              width: 180, flexShrink: 0, borderRight: "1px solid var(--line)",
              overflowY: "auto", padding: "10px 6px",
              background: "var(--surface-1)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "2px 8px 6px" }}>
              <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".08em", color: "var(--fg-mute)", textTransform: "uppercase" }}>
                Subfolders
              </span>
              <button
                className="icon-btn"
                style={{ width: 20, height: 20, fontSize: 16, lineHeight: 1 }}
                title="Create subfolder"
                onClick={() => { setShowCreateSubfolder(true); setNewSubfolderName(""); }}
              >+</button>
            </div>
            {showCreateSubfolder && (
              <div style={{ padding: "0 6px 6px" }}>
                <input
                  className="input"
                  style={{ width: "100%", fontSize: 12, padding: "3px 6px" }}
                  placeholder="characters/poses"
                  value={newSubfolderName}
                  onChange={(e) => setNewSubfolderName(e.target.value)}
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && newSubfolderName.trim()) createSubfolderMutation.mutate(newSubfolderName.trim());
                    if (e.key === "Escape") { setShowCreateSubfolder(false); setNewSubfolderName(""); }
                  }}
                />
                <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                  <button
                    className="btn sm"
                    style={{ flex: 1, fontSize: 11 }}
                    disabled={!newSubfolderName.trim() || createSubfolderMutation.isPending}
                    onClick={() => createSubfolderMutation.mutate(newSubfolderName.trim())}
                  >Create</button>
                  <button
                    className="btn ghost sm"
                    style={{ fontSize: 11 }}
                    onClick={() => { setShowCreateSubfolder(false); setNewSubfolderName(""); }}
                  >✕</button>
                </div>
              </div>
            )}
            {/* "All" entry */}
            <button
              onClick={() => { setActiveSubfolder(undefined); resetPage(); }}
              style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                width: "100%", padding: "5px 8px", borderRadius: "var(--r)",
                border: "none", cursor: "pointer", textAlign: "left",
                background: activeSubfolder === undefined ? "var(--surface-3)" : "transparent",
                color: activeSubfolder === undefined ? "var(--accent)" : "var(--fg)",
                fontSize: 12.5, fontWeight: activeSubfolder === undefined ? 600 : 400,
              }}
            >
              <span>All</span>
              <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>{dataset?.image_count ?? ""}</span>
            </button>
            {/* (root) entry — images with no subfolder. Rendered even when empty so it
                stays available as a drop target for dragging images back out. */}
            <DropZone id={subfolderDropId("")}>
              {({ setNodeRef, isOver }) => (
              <div
                ref={setNodeRef}
                className="subfolder-row"
                style={{
                  display: "flex", alignItems: "center",
                  borderRadius: "var(--r)",
                  background: isOver || isRootActive ? "var(--surface-3)" : "transparent",
                  boxShadow: isOver ? "inset 0 0 0 1px var(--accent)" : "none",
                }}
              >
                <button
                  onClick={() => { setActiveSubfolder(""); resetPage(); }}
                  title="(root)"
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    flex: 1, minWidth: 0, padding: "5px 4px 5px 8px",
                    border: "none", cursor: "pointer", textAlign: "left",
                    background: "transparent",
                    color: isRootActive ? "var(--accent)" : "var(--fg)",
                    fontSize: 12.5, fontWeight: isRootActive ? 600 : 400, fontStyle: "italic",
                  }}
                >
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>(root)</span>
                  <span style={{ fontSize: 11, color: "var(--fg-mute)", marginLeft: 4, flexShrink: 0 }}>{rootEntry.image_count}</span>
                </button>
                {rootEntry.image_count > 0 && (
                  <>
                    <button
                      className="subfolder-action-btn subfolder-move-btn"
                      title={`Move "(root)" to another dataset`}
                      onClick={(e) => { e.stopPropagation(); setPendingMoveSubfolder(rootEntry); }}
                    ><ArrowRightFromLine size={11} /></button>
                    <button
                      className="subfolder-action-btn subfolder-copy-btn"
                      title={`Copy "(root)" to another dataset`}
                      onClick={(e) => { e.stopPropagation(); setPendingCopySubfolder(rootEntry); }}
                    ><Copy size={11} /></button>
                  </>
                )}
              </div>
              )}
            </DropZone>
            {/* nested subfolder tree */}
            {subfolderTree.map(renderSubfolderNode)}
          </div>
          )}
          </DropZone>
        )}

        {/* Main grid column */}
        <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
        {isDragOver && (
          <div style={{
            position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none",
            background: "rgba(0,0,0,0.55)", border: "2px dashed var(--accent)",
            borderRadius: "var(--r-lg)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <div style={{ textAlign: "center" }}>
              <svg width="40" height="40" viewBox="0 0 16 16" fill="none" stroke="var(--accent)" strokeWidth="1.2">
                <path d="M8 10V2M5 5l3-3 3 3M2.5 13.5h11"/>
              </svg>
              <p style={{ margin: "12px 0 0", color: "var(--accent)", fontSize: 15, fontWeight: 600 }}>
                Drop images to upload
              </p>
            </div>
          </div>
        )}
        <div
          ref={scrollRef}
          style={{ height: "100%", overflowY: "auto", padding: "18px 28px" }}
          onDragEnter={handleDragEnter}
          onDragOver={(e) => e.preventDefault()}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
        {isLoading ? (
          <div style={{ textAlign: "center", marginTop: 80, color: "var(--fg-mute)" }}>Loading…</div>
        ) : images.length === 0 ? (
          <div className="empty-state">
            <p>No images found. Upload or adjust filters.</p>
            <label className="btn primary" style={{ cursor: "pointer" }}>
              Upload images
              <input type="file" multiple accept={MEDIA_ACCEPT} style={{ display: "none" }}
                onChange={(e) => e.target.files && handleUpload(e.target.files)} />
            </label>
          </div>
        ) : (
          (() => {
            // Cards are draggable in every sort mode (so they can be dropped on a
            // subfolder row); only sort-reordering is gated on custom order.
            const grid = (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
                {images.map((img) => (
                  <SortableImageCard
                    key={img.id}
                    image={img}
                    sortable={isCustomOrder}
                    onShowGenMeta={img.generation_metadata ? setGenMetaImage : undefined}
                    onSelect={handleSelect}
                  />
                ))}
              </div>
            );
            return isCustomOrder ? (
              <SortableContext items={images.map(i => i.id)} strategy={rectSortingStrategy}>
                {grid}
              </SortableContext>
            ) : grid;
          })()
        )}

        {/* Pagination */}
        {(page > 1 || images.length === pageSize) && (
          <div style={{ display: "flex", justifyContent: "center", gap: 12, marginTop: 24 }}>
            {page > 1 && <button className="btn" onClick={() => setPage(p => p - 1)}>← Previous</button>}
            <span style={{ alignSelf: "center", fontSize: 12, color: "var(--fg-mute)" }}>Page {page}</span>
            {images.length === pageSize && <button className="btn" onClick={() => setPage(p => p + 1)}>Next →</button>}
          </div>
        )}

        {/* Selection bar (sticky bottom within scroll area) */}
        <SelectionToolbar datasetId={datasetId!} subfolders={subfolders} />
        </div>
        </div>
      </div>

      {/* Drag preview. Portaled out so it isn't clipped by the grid's overflow-y
          container when dragged over the sidebar. */}
      {createPortal(
        <DragOverlay dropAnimation={null} zIndex={60}>
          {activeDragImage && (
            <div style={{
              position: "relative", pointerEvents: "none", opacity: 0.92,
              boxShadow: "0 12px 32px -8px rgba(0,0,0,.6)", borderRadius: "var(--r-lg)",
            }}>
              <ImageCard image={activeDragImage} />
              {activeDragCount > 1 && (
                <span style={{
                  position: "absolute", top: -8, right: -8, zIndex: 2,
                  padding: "2px 8px", borderRadius: 999,
                  background: "var(--accent)", color: "#03130d",
                  fontSize: 11, fontWeight: 600, fontFamily: '"Geist Mono", monospace',
                }}>
                  {activeDragCount} images
                </span>
              )}
            </div>
          )}
        </DragOverlay>,
        document.body
      )}
      </DndContext>

      {/* Generation metadata modal */}
      {genMetaImage?.generation_metadata && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 50,
            background: "rgba(0,0,0,.6)", backdropFilter: "blur(4px)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
          onClick={() => setGenMetaImage(null)}
        >
          <div
            style={{
              background: "var(--surface-1)", border: "1px solid var(--line)",
              borderRadius: "var(--r-lg)", padding: "16px 20px",
              width: 480, maxWidth: "90vw", maxHeight: "80vh", overflowY: "auto",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: "var(--fg)" }}>
                {genMetaImage.filename}
              </span>
              <button
                className="icon-btn"
                style={{ width: 24, height: 24 }}
                onClick={() => setGenMetaImage(null)}
              >
                ×
              </button>
            </div>
            <GenerationMetadata metadata={genMetaImage.generation_metadata} />
          </div>
        </div>
      )}

      {showRenumberConfirm && (
        <ConfirmDialog
          title="Renumber Files"
          message={`Rename images to sequential names (${activeSubfolder ? (activeSubfolder.split("/").pop() || "image") : "image"}_001, _002, …) in current custom order?`}
          confirmLabel={renumberMutation.isPending ? "Renaming…" : "Renumber"}
          onConfirm={() => renumberMutation.mutate()}
          onCancel={() => setShowRenumberConfirm(false)}
        />
      )}

      {pendingDeleteSubfolder && (
        <ConfirmDialog
          title={`Delete "${pendingDeleteSubfolder.path || "(root)"}"`}
          message={deleteDialogMessage}
          confirmLabel="Delete Subfolder"
          danger
          onConfirm={() => deleteSubfolderMutation.mutate(pendingDeleteSubfolder.path)}
          onCancel={() => setPendingDeleteSubfolder(null)}
        />
      )}

      {pendingMoveSubfolder && (
        <MoveToDatasetModal
          count={pendingMoveSubfolder.image_count}
          currentDatasetId={datasetId!}
          isPending={moveSubfolderToDatasetMutation.isPending}
          onConfirm={(targetId, subfolder) => moveSubfolderToDatasetMutation.mutate({ targetId, subfolder })}
          onClose={() => setPendingMoveSubfolder(null)}
        />
      )}

      {pendingCopySubfolder && (
        <MoveToDatasetModal
          mode="copy"
          count={pendingCopySubfolder.image_count}
          currentDatasetId={datasetId!}
          isPending={copySubfolderToDatasetMutation.isPending}
          onConfirm={(targetId, subfolder) => copySubfolderToDatasetMutation.mutate({ targetId, subfolder })}
          onClose={() => setPendingCopySubfolder(null)}
        />
      )}

      {showImport && (
        <ImportFolderModal
          datasets={allDatasets.length ? allDatasets : dataset ? [dataset] : []}
          initialDatasetId={datasetId ?? undefined}
          onStarted={setImportJobId}
          onClose={() => setShowImport(false)}
        />
      )}
    </div>
  );
}
