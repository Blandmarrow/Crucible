import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { ArrowRightFromLine, Copy } from "lucide-react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, rectSortingStrategy, arrayMove } from "@dnd-kit/sortable";
import { imagesApi } from "../api/images";
import type { ImageListItem, SubfolderInfo } from "../types";
import GenerationMetadata from "../components/image/GenerationMetadata";
import ConfirmDialog from "../components/common/ConfirmDialog";
import MoveToDatasetModal from "../components/common/MoveToDatasetModal";
import { datasetsApi } from "../api/datasets";
import ImageCard, { SortableImageCard } from "../components/gallery/ImageCard";
import SelectionToolbar from "../components/gallery/SelectionToolbar";
import { useSelectionStore } from "../store/selectionStore";
import { useUploadStore } from "../store/uploadStore";
import { getGalleryPageSize, getGalleryDefaultSort, getGalleryDefaultCaptionFilter, getGalleryDefaultQualityFilter } from "../constants/storage";
import { SORT_OPTIONS } from "../constants/galleryOptions";

type QualityFilter = "" | "is_blurry" | "is_noisy" | "is_uniform" | "has_watermark" | "is_duplicate";

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
    const raw = sessionStorage.getItem(`gallery-state-${datasetId}`);
    if (raw) return JSON.parse(raw) as { page: number; sortIdx: number; captionedFilter: boolean | null; qualityFilter?: string; scrollTop: number; activeSubfolder?: string | null };
  } catch {}
  return null;
}

export default function GalleryPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const { selectAll, clear, count, toggle, replaceRange } = useSelectionStore();

  const pageSize = useMemo(getGalleryPageSize, []);

  const saved = useMemo(() => (datasetId ? loadSavedState(datasetId) : null), [datasetId]);
  const [page, setPage] = useState(saved?.page ?? 1);
  const [sortIdx, setSortIdx] = useState(saved?.sortIdx ?? getGalleryDefaultSort());
  const [captionedFilter, setCaptionedFilter] = useState<boolean | undefined>(
    saved?.captionedFilter == null ? getGalleryDefaultCaptionFilter() : saved.captionedFilter
  );
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
  const liveState = useRef({ page, sortIdx, captionedFilter, qualityFilter, activeSubfolder });
  liveState.current = { page, sortIdx, captionedFilter, qualityFilter, activeSubfolder };
  const [showRenumberConfirm, setShowRenumberConfirm] = useState(false);
  const prevSortIdxRef = useRef(sortIdx);
  const imagesRef = useRef<ImageListItem[]>([]);
  const lastSelectedId = useRef<string | null>(null);
  const lastRangeEndId = useRef<string | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));

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

  useEffect(() => {
    return () => {
      const scrollTop = scrollRef.current?.scrollTop ?? 0;
      const { page, sortIdx, captionedFilter, qualityFilter, activeSubfolder } = liveState.current;
      if (datasetId) {
        sessionStorage.setItem(
          `gallery-state-${datasetId}`,
          JSON.stringify({ page, sortIdx, captionedFilter: captionedFilter ?? null, qualityFilter, scrollTop, activeSubfolder: activeSubfolder ?? null })
        );
      }
    };
  }, [datasetId]);

  const { data: dataset } = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => datasetsApi.get(datasetId!),
    enabled: !!datasetId,
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

  const scoreFiltersParam = scoreFilters.length > 0
    ? JSON.stringify(scoreFilters.map(f => ({
        field: f.field,
        min: f.min !== "" ? parseFloat(f.min) : undefined,
        max: f.max !== "" ? parseFloat(f.max) : undefined,
      })))
    : undefined;

  const imagesQueryKey = useMemo(
    () => ["images", datasetId, page, pageSize, sortOpt, captionedFilter, qualityFilter, search, scoreFiltersParam, activeSubfolder, detectionLabel],
    [datasetId, page, pageSize, sortOpt, captionedFilter, qualityFilter, search, scoreFiltersParam, activeSubfolder, detectionLabel]
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

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id || !datasetId) return;
    const cached = qc.getQueryData<ImageListItem[]>(imagesQueryKey) ?? [];
    const oldIndex = cached.findIndex(img => img.id === active.id);
    const newIndex = cached.findIndex(img => img.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    const newOrder = arrayMove(cached, oldIndex, newIndex);
    qc.setQueryData(imagesQueryKey, newOrder);
    const pageOffset = (page - 1) * pageSize;
    reorderMutation.mutate(newOrder.map((img, idx) => ({ id: img.id, sort_order: pageOffset + idx })));
  }, [qc, imagesQueryKey, datasetId, page, pageSize, reorderMutation]);

  const handleUpload = useCallback(async (files: FileList, sf?: string) => {
    if (!datasetId) return;
    const fileArray = Array.from(files);
    const subfolder = sf ?? uploadSubfolder;
    setUploadProgress({ datasetId, done: 0, total: fileArray.length, errors: 0 });
    let errors = 0;
    for (let i = 0; i < fileArray.length; i++) {
      try {
        await imagesApi.uploadSingle(datasetId, fileArray[i], subfolder);
        // Invalidate after each success so images appear in the gallery live.
        // cancelRefetch: false lets in-flight fetches finish instead of being
        // restarted on every file, coalescing rapid invalidations into fewer GETs.
        qc.invalidateQueries({ queryKey: ["images", datasetId] }, { cancelRefetch: false });
      } catch {
        errors++;
      }
      setUploadProgress({ datasetId, done: i + 1, total: fileArray.length, errors });
    }
    // Final refresh to ensure the gallery is fully up-to-date
    await refetch();
    qc.invalidateQueries({ queryKey: ["datasets"] });
    qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
    const succeeded = fileArray.length - errors;
    if (succeeded > 0) toast.success(`Uploaded ${succeeded} image(s)`);
    if (errors > 0) toast.error(`${errors} file(s) failed to upload`);
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
      toast.success(`Copied ${data.copied} image${data.copied !== 1 ? "s" : ""} to dataset`);
      setPendingCopySubfolder(null);
    },
    onError: () => toast.error("Copy to dataset failed"),
  });

  const handleDragEnter = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes("Files")) {
      e.preventDefault();
      setIsDragOver(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    const related = e.relatedTarget as Node | null;
    if (!related || !e.currentTarget.contains(related)) setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (!uploading && e.dataTransfer.files.length) handleUpload(e.dataTransfer.files);
  };

  const flaggedCount = dataset ? (dataset.image_count - dataset.captioned_count) : 0; // placeholder

  const resetPage = () => { setPage(1); hasRestoredScroll.current = false; };

  const applyScoreFilter = () => {
    if (!draftMin && !draftMax) return;
    setScoreFilters(prev => [...prev, { field: draftField, min: draftMin, max: draftMax }]);
    setDraftMin("");
    setDraftMax("");
    setShowAddScore(false);
    resetPage();
  };

  const rootEntry = subfolders.find(sf => sf.path === "");
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
        <div
          className="subfolder-row"
          style={{
            display: "flex", alignItems: "center",
            borderRadius: "var(--r)",
            background: isActive ? "var(--surface-3)" : "transparent",
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
        </select>

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
          <label className="btn" style={{ cursor: uploading ? "default" : "pointer", opacity: uploading ? 0.65 : 1, pointerEvents: uploading ? "none" : "auto" }}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M8 10V2M5 5l3-3 3 3M2.5 13.5h11"/>
            </svg>
            {uploading ? "Uploading…" : "Upload"}
            <input type="file" multiple accept="image/*" style={{ display: "none" }}
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

      {/* Grid area with subfolder sidebar */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* Subfolder sidebar */}
        {(subfolders.length > 0 || showCreateSubfolder) && (
          <div style={{
            width: 180, flexShrink: 0, borderRight: "1px solid var(--line)",
            overflowY: "auto", padding: "10px 6px",
            background: "var(--surface-1)",
          }}>
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
            {/* (root) entry — images with no subfolder */}
            {rootEntry && (
              <div
                className="subfolder-row"
                style={{
                  display: "flex", alignItems: "center",
                  borderRadius: "var(--r)",
                  background: isRootActive ? "var(--surface-3)" : "transparent",
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
              </div>
            )}
            {/* nested subfolder tree */}
            {subfolderTree.map(renderSubfolderNode)}
          </div>
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
              <input type="file" multiple accept="image/*" style={{ display: "none" }}
                onChange={(e) => e.target.files && handleUpload(e.target.files)} />
            </label>
          </div>
        ) : (
          (() => {
            const grid = (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
                {images.map((img) =>
                  isCustomOrder ? (
                    <SortableImageCard
                      key={img.id}
                      image={img}
                      onShowGenMeta={img.generation_metadata ? setGenMetaImage : undefined}
                      onSelect={handleSelect}
                    />
                  ) : (
                    <ImageCard
                      key={img.id}
                      image={img}
                      onShowGenMeta={img.generation_metadata ? setGenMetaImage : undefined}
                      onSelect={handleSelect}
                    />
                  )
                )}
              </div>
            );
            return isCustomOrder ? (
              <DndContext collisionDetection={closestCenter} onDragEnd={handleDragEnd} sensors={sensors}>
                <SortableContext items={images.map(i => i.id)} strategy={rectSortingStrategy}>
                  {grid}
                </SortableContext>
              </DndContext>
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
    </div>
  );
}
