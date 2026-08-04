import { useState, useCallback, useEffect, useRef, useMemo, type CSSProperties } from "react";
import { ArrowRightFromLine, Copy, Edit2, Folder, FolderInput, Plus, Trash2 } from "lucide-react";
import { usePaneDatasetId, usePaneGallerySourceVideo, usePaneGallerySubfolder } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../utils/apiError";
import { createPortal } from "react-dom";
import {
  DndContext, DragOverlay, closestCenter, pointerWithin, PointerSensor, useSensor, useSensors,
  type DragEndEvent, type DragStartEvent, type DragOverEvent, type CollisionDetection,
} from "@dnd-kit/core";
import { SortableContext, rectSortingStrategy, arrayMove } from "@dnd-kit/sortable";
import { imagesApi, type ImageFilterParams, type UploadResult } from "../api/images";
import type { ImageListItem, SubfolderInfo } from "../types";
import GenerationMetadata from "../components/image/GenerationMetadata";
import ConfirmDialog from "../components/common/ConfirmDialog";
import MoveToDatasetModal from "../components/common/MoveToDatasetModal";
import ImportFolderModal from "../components/common/ImportFolderModal";
import { datasetsApi } from "../api/datasets";
import { jobsApi } from "../api/jobs";
import { showImportSummaryToast } from "../utils/importToast";
import { writeNavContext } from "../utils/galleryNav";
import { showUploadSummaryToast, tallyUpload } from "../utils/uploadToast";
import ImageCard, { SortableImageCard } from "../components/gallery/ImageCard";
import DropZone from "../components/gallery/DropZone";
import SubfolderRowDnd from "../components/gallery/SubfolderRowDnd";
import MoveSubfolderModal from "../components/gallery/MoveSubfolderModal";
import ContextMenu, { type ContextMenuAction } from "../components/common/ContextMenu";
import SelectionToolbar from "../components/gallery/SelectionToolbar";
import VideoStrip from "../components/gallery/VideoStrip";
import { videosApi } from "../api/videos";
import { useSelectionStore } from "../store/selectionStore";
import { useUploadStore } from "../store/uploadStore";
import { useJobStore } from "../store/jobStore";
import { settingsApi } from "../api/settings";
import { getGalleryPageSize, getGalleryDefaultSort, getGalleryDefaultCaptionFilter, getGalleryDefaultQualityFilter, SUBFOLDER_RENAME_KEY, PERSIST_DEBOUNCE_MS } from "../constants/storage";
import { LICENSE_OPTIONS, OTHER_PREFIX, isKnownLicenseValue } from "../constants/licenses";
import { useCustomLicenses } from "../hooks/useCustomLicenses";
import { MISSING_LICENSE, SORT_OPTIONS, canDropFolderOn, isSubfolderDragId, isSubfolderDropId, subfolderDragId, subfolderDropId, subfolderFromDragId, subfolderFromDropId, SIDEBAR_DROP_ID } from "../constants/galleryOptions";
import { MEDIA_ACCEPT, isMediaDragItem, isMediaFile } from "../constants/mediaTypes";
import { invalidateDatasetContentScope } from "../constants/queryKeys";

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
  { value: "luminance_score",        label: "Brightness (0–1)",  short: "Bright"     },
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

/** Every proper ancestor of a subfolder path: "a/b/c" → ["a", "a/b"]. The set of
 *  `expandedPaths` keys that have to be open for that path's row to be visible. */
function ancestorPaths(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
}

/** Is `path` `root` itself or filed anywhere beneath it? The subtree predicate every
 *  path-keyed piece of sidebar state is re-pointed or pruned by. `isInSubtree("", root)`
 *  is false for any named `root`, so the dataset root is never caught by one. */
function isInSubtree(path: string, root: string): boolean {
  return path === root || path.startsWith(root + "/");
}

/** Returns the *same* set when nothing matched, so it never forces a render — or a
 *  needless write to `gallery-state-${datasetId}`. */
function withoutSubtree(set: Set<string>, root: string): Set<string> {
  const kept = [...set].filter(p => !isInSubtree(p, root));
  if (kept.length === set.size) return set;
  return new Set(kept);
}

/** The expand/collapse cell's box, which doubles as the row's indent. Shared by the
 *  branch row's <button> and the leaf row's inert spacer so the two cannot drift out of
 *  alignment — `box-sizing: border-box` is global, so they measure identically. */
function toggleCellStyle(depth: number): CSSProperties {
  return {
    flexShrink: 0,
    width: 8 + depth * 12 + 12,
    minHeight: 28, border: "none",
    background: "transparent",
    color: "var(--fg-mute)", fontSize: 7,
    paddingLeft: 8 + depth * 12,
    display: "flex", alignItems: "center", justifyContent: "flex-end", paddingRight: 2,
  };
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
    if (raw) return JSON.parse(raw) as { page: number; sortIdx: number; captionedFilter: boolean | null; qualityFilter?: string; licenseFilter?: string; scrollTop: number; activeSubfolder?: string | null; frameVideoId?: string; expandedPaths?: string[] };
  } catch {}
  return null;
}

export default function GalleryPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const { selectMany, deselectMany, clearDataset, clear, toggle, replaceRange, selectedIds, datasetByImageId } = useSelectionStore();

  const pageSize = useMemo(getGalleryPageSize, []);

  const saved = useMemo(() => (datasetId ? loadSavedState(datasetId) : null), [datasetId]);
  // Declared up here, above the deep-link render-adjust blocks that call
  // `dropScrollRestore`. Refs have no ordering dependencies.
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasRestoredScroll = useRef(false);
  // A filter change or a deep link lands at the top of the *new* list. The saved
  // offset in `gallery-state-*` belongs to the list the user left, so applying it
  // here drops them into the middle of a different result set — or nowhere, if the
  // new list is shorter. `pendingScrollTop` is consumed by the scroll effect once
  // the new page has actually rendered; setting scrollTop here would be a DOM write
  // during render, and would fire before the rows exist.
  const pendingScrollTop = useRef(false);
  const dropScrollRestore = () => {
    hasRestoredScroll.current = true;
    pendingScrollTop.current = true;
  };
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
  // A link that asked for a particular subfolder (video extraction history, and
  // the Phase 3 lineage filter after it). Applied by the effect below rather than
  // read directly: `activeSubfolder` is otherwise restored from localStorage and
  // then owned by the sidebar, and a deep link must not take that ownership away.
  const linkedSubfolder = usePaneGallerySubfolder();
  const [appliedSubfolder, setAppliedSubfolder] = useState<string | undefined>(undefined);
  const [uploadSubfolder, setUploadSubfolder] = useState("");
  // The lineage filter: every frame a video produced, wherever curation has since
  // filed it. Declared above both render-adjust blocks because each clears the
  // other's filter — see the comments in them.
  const [frameVideoId, setFrameVideoId] = useState<string | undefined>(saved?.frameVideoId || undefined);
  // Apply the deep link on arrival, and again whenever the incoming value
  // *changes* — but never twice for the same value, or it would fight a user who
  // arrived here and then clicked a different folder in the sidebar. `undefined`
  // means "no link asked for anything", which is why this records the last
  // applied value rather than a boolean: "" is a real target (the dataset root).
  // Adjusted during render, not in an effect, so the previous subfolder's images
  // are never fetched and painted first.
  if (linkedSubfolder !== undefined && appliedSubfolder !== linkedSubfolder) {
    setAppliedSubfolder(linkedSubfolder);
    setActiveSubfolder(linkedSubfolder);
    // The mirror of the lineage branch below: a `frameVideoId` restored from
    // `gallery-state-${datasetId}` would intersect the linked subfolder and show an
    // empty grid, right after the history panel named a frame count for it.
    setFrameVideoId(undefined);
    setPage(1);
    dropScrollRestore();
  }
  // Same deep-link discipline as `appliedSubfolder` above — applied once per
  // incoming *change*, so the "Frames from" select stays the user's afterwards.
  const linkedVideo = usePaneGallerySourceVideo();
  const [appliedVideo, setAppliedVideo] = useState<string | undefined>(undefined);
  if (linkedVideo !== undefined && appliedVideo !== linkedVideo) {
    setAppliedVideo(linkedVideo);
    setFrameVideoId(linkedVideo);
    // Load-bearing: arriving via `?source_video_id=` leaves `linkedSubfolder`
    // undefined, so a subfolder restored from localStorage would silently
    // intersect the lineage filter and show an empty grid. Lineage spans
    // subfolders — that is the whole point of it.
    setActiveSubfolder(undefined);
    setPage(1);
    dropScrollRestore();
  }
  const [showCreateSubfolder, setShowCreateSubfolder] = useState(false);
  const [newSubfolderName, setNewSubfolderName] = useState("");
  const [pendingDeleteSubfolder, setPendingDeleteSubfolder] = useState<SubfolderInfo | null>(null);
  const [pendingMoveSubfolder, setPendingMoveSubfolder] = useState<SubfolderInfo | null>(null);
  const [pendingCopySubfolder, setPendingCopySubfolder] = useState<SubfolderInfo | null>(null);
  // Persisted as a string[] in the gallery-state blob (Sets are not JSON), because
  // GalleryPage unmounts on every trip to the image detail view — without this the tree
  // came back fully collapsed around a still-selected deep folder. Array-guarded: the
  // blob can have been written by a build that did not carry this field.
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(
    () => new Set(Array.isArray(saved?.expandedPaths) ? saved.expandedPaths : [])
  );
  const [createChildOf, setCreateChildOf] = useState<string | null>(null);
  const [newChildName, setNewChildName] = useState("");
  // Right-click menu on a named subfolder row. Rendered with the other overlays, not
  // inside renderSubfolderNode — the node it describes may collapse out from under it.
  const [folderMenu, setFolderMenu] = useState<{ x: number; y: number; node: SubfolderNode } | null>(null);
  const [pendingRepathSubfolder, setPendingRepathSubfolder] = useState<SubfolderNode | null>(null);
  // Inline rename. Single-segment by construction (see the input's onChange).
  const [renamingPath, setRenamingPath] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const sortOpt = SORT_OPTIONS[sortIdx];
  const isCustomOrder = sortOpt.sort === "sort_order";
  const liveStateRef = useRef({ page, sortIdx, captionedFilter, qualityFilter, licenseFilter, activeSubfolder, frameVideoId, expandedPaths });
  liveStateRef.current = { page, sortIdx, captionedFilter, qualityFilter, licenseFilter, activeSubfolder, frameVideoId, expandedPaths };
  const [showRenumberConfirm, setShowRenumberConfirm] = useState(false);
  const prevSortIdxRef = useRef(sortIdx);
  const imagesRef = useRef<ImageListItem[]>([]);
  const lastSelectedId = useRef<string | null>(null);
  const lastRangeEndId = useRef<string | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  // `null` = not over a folder row; `""` = the (root) row, which is a real target.
  const [dropTargetPath, setDropTargetPath] = useState<string | null>(null);

  // closestCenter alone is wrong once the 180px sidebar rows are droppables (a card
  // dragged near the grid's left edge can be "closest" to a row), but pointerWithin
  // alone makes reordering feel dead in the gutters between cards. Compose the two.
  const collisionDetection = useCallback<CollisionDetection>((args) => {
    const pointer = pointerWithin(args);
    // A folder drag short-circuits everything below. Filtering *here* rather than only
    // guarding at drag end is what keeps `isOver` honest — otherwise a self-or-descendant
    // row lights up with the accent ring and then refuses the drop. It also stops a folder
    // drag ever resolving to a grid card (pointerWithin over the grid hands back a card,
    // which survives today only because findIndex returns -1) and bypasses the sidebar
    // sentinel, which exists to guard a reorder a folder drag can never do.
    if (isSubfolderDragId(args.active.id)) {
      const src = subfolderFromDragId(args.active.id);
      const hit = pointer.find(
        c => isSubfolderDropId(c.id) && canDropFolderOn(src, subfolderFromDropId(c.id))
      );
      return hit ? [hit] : [];
    }
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

  // Both debounce effects bail when the input already equals the committed value.
  // That guard is what makes the page/scroll restore stick: without it the effect
  // fires once on *mount* — 350ms after arriving back from the detail view — and
  // resets `page` to 1, throwing away the page just restored from localStorage
  // (which the persist effect then overwrites with 1, so the loss is permanent).
  // It also covers typing a query and deleting it again before the timer: nothing
  // changed, so the page must not jump.
  useEffect(() => {
    if (searchInput === search) return;
    const t = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
      dropScrollRestore();
    }, 350);
    return () => clearTimeout(t);
  }, [searchInput, search]);

  useEffect(() => {
    if (detectionLabelInput === detectionLabel) return;
    const t = setTimeout(() => {
      setDetectionLabel(detectionLabelInput);
      setPage(1);
      dropScrollRestore();
    }, 350);
    return () => clearTimeout(t);
  }, [detectionLabelInput, detectionLabel]);

  // Selecting a folder opens it, and opens everything above it: the path itself is what makes
  // picking a folder show what is filed under it, the way every other tree behaves, and the
  // ancestors are what make the row visible at all.
  //
  // The path *itself* goes in only when the selection actually changed since the last run,
  // which a dep array alone does not give you: an effect fires on **mount** whether or not
  // its dep moved, and GalleryPage unmounts on every trip to the image detail view. Without
  // `seenSubfolder` the return trip re-added `activeSubfolder`, re-opening a folder the user
  // deliberately collapsed while standing in it — the persisted blob had it right and this
  // effect overrode it. Four traps, each load-bearing:
  //
  //  - It remembers the *value*, not a `useRef(true)` first-run flag. StrictMode
  //    double-invokes mount effects in dev, so a flag flipped by run #1 makes run #2 look
  //    like a real change and the bug comes back — only under `npm run dev`, since the e2e
  //    suite serves the production bundle where StrictMode is inert. A value makes both runs
  //    decide the same thing.
  //  - The seed is `undefined` when a link named a subfolder, so the effect sees a change and
  //    opens it: a deep link is an arrival, not a restore. `usePaneGallerySubfolder` returns
  //    `undefined` for "no link asked for anything" and `""` for a real link to the dataset
  //    root, so the test is `!== undefined` and never truthiness.
  //  - Ordering holds: the render-phase apply block above runs before the commit, so on a
  //    deep-linked mount the effect already sees the linked value.
  //  - The ref is written *before* the `!activeSubfolder` bail. Arriving with
  //    `?source_video_id=` clears the selection; a stale value left behind would make the
  //    user's next click on that same folder look like a no-op and not open it.
  //
  // Ancestors go in on every run, by design: they are what make the row *reachable*, and
  // `activeSubfolder` arrives from three places that know nothing about the tree's shape (the
  // restored blob, the deep link applied during render, and a sidebar click), the first two of
  // which can name a folder nested inside closed branches. So collapsing `alpha` while standing
  // in `alpha/inner` does re-open `alpha` on the way back — otherwise the active row is off
  // screen — while `alpha/inner` itself stays shut. Returning `prev` unchanged when everything
  // is already open is what stops this re-running itself. A childless path is harmless in the
  // set — both the toggle and the render bail on `hasChildren`.
  const seenSubfolder = useRef<string | undefined>(
    linkedSubfolder !== undefined ? undefined : (saved?.activeSubfolder ?? undefined)
  );
  useEffect(() => {
    const previous = seenSubfolder.current;
    seenSubfolder.current = activeSubfolder;
    if (!activeSubfolder) return;
    const needed = ancestorPaths(activeSubfolder);
    if (activeSubfolder !== previous) needed.push(activeSubfolder);
    setExpandedPaths(prev => {
      if (needed.every(p => prev.has(p))) return prev;
      const next = new Set(prev);
      for (const p of needed) next.add(p);
      return next;
    });
  }, [activeSubfolder]);

  // Persist gallery state (page/sort/filters) — debounced, survives browser restart.
  // Hand-rolled rather than useDebouncedPersist because `scrollTop` must be sampled
  // at flush time; the window is the same shared constant either way.
  useEffect(() => {
    if (!datasetId) return;
    const t = setTimeout(() => {
      const scrollTop = scrollRef.current?.scrollTop ?? 0;
      localStorage.setItem(
        `gallery-state-${datasetId}`,
        JSON.stringify({ page, sortIdx, captionedFilter: captionedFilter ?? null, qualityFilter, licenseFilter, scrollTop, activeSubfolder: activeSubfolder ?? null, frameVideoId: frameVideoId ?? "", expandedPaths: [...expandedPaths] })
      );
    }, PERSIST_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [datasetId, page, sortIdx, captionedFilter, qualityFilter, licenseFilter, activeSubfolder, frameVideoId, expandedPaths]);

  // Save precise scroll position + current state on unmount via ref — avoids stale localStorage reads
  // and the debounce gap where a <350ms navigation would otherwise lose state changes.
  useEffect(() => {
    return () => {
      if (!datasetId) return;
      const scrollTop = scrollRef.current?.scrollTop ?? 0;
      const { page, sortIdx, captionedFilter, qualityFilter, licenseFilter, activeSubfolder, frameVideoId, expandedPaths } = liveStateRef.current;
      try {
        localStorage.setItem(
          `gallery-state-${datasetId}`,
          JSON.stringify({ page, sortIdx, captionedFilter: captionedFilter ?? null, qualityFilter, licenseFilter, scrollTop, activeSubfolder: activeSubfolder ?? null, frameVideoId: frameVideoId ?? "", expandedPaths: [...expandedPaths] })
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

  // The dataset's videos, for the "Frames from" select. Same query key as
  // `VideoStrip`, so this shares its cache entry rather than fetching again.
  const { data: videos, isSuccess: videosLoaded } = useQuery({
    queryKey: ["videos", datasetId],
    queryFn: () => videosApi.list(datasetId!),
    enabled: !!datasetId,
  });
  // Stale-id guard, derived during render like `appliedSubfolder` above. A video
  // deleted (or a filter restored into a different dataset) would otherwise leave
  // a permanently empty grid behind a `<select>` rendering blank — the same class
  // of problem `licenseFilter`'s vocabulary bounds-check solves.
  if (frameVideoId && videosLoaded && !videos?.some(v => v.id === frameVideoId)) {
    setFrameVideoId(undefined);
  }

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
    invalidateDatasetContentScope(qc, datasetId);
    // A rescan adopts clips out of videos/ too, and its toast reports the count.
    // Without this the header badge updates (it reads `dataset.video_count`) but
    // `VideoStrip` keeps its pre-rescan list for the 30 s staleTime — the page
    // says "12 videos" above a strip that renders nothing, and Extract frames is
    // unreachable until the tab loses and regains focus.
    qc.invalidateQueries({ queryKey: ["videos", datasetId] });
    if (rescanManualRef.current) {
      const jid = rescanJobId;
      jobsApi.get(jid).then((job) => {
        const r = job.result_data as {
          added?: number; renamed?: number; captions_updated?: number; missing?: unknown[];
          videos_added?: number; videos_missing?: unknown[];
        };
        const missing = (r.missing ?? []).length + (r.videos_missing ?? []).length;
        toast.success(
          `Rescan complete — ${r.added ?? 0} added, ${r.captions_updated ?? 0} caption(s) updated` +
          (r.videos_added ? `, ${r.videos_added} video(s) added` : "") +
          // Called out because rescan otherwise never touches a file: a name
          // changing under the user has to be visible, not inferred later.
          (r.renamed ? `, ${r.renamed} renamed to avoid a name clash` : "") +
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
    invalidateDatasetContentScope(qc, datasetId);
    // An import is a provenance writer: a scraper sidecar is the largest source
    // of new `other:` licenses, and they are unpickable until this refetches.
    qc.invalidateQueries({ queryKey: ["licenses-in-use", datasetId] });
    // Same as the rescan handler above: `include_videos` creates Video rows.
    qc.invalidateQueries({ queryKey: ["videos", datasetId] });
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

  // One description of "the current view", shared by the grid, the count and
  // select-all. Built here rather than inline in the list `queryFn` so the offer
  // ("select all 1,240 matching filters") can never name a different set of
  // images than the button then grabs.
  const filterParams = useMemo<ImageFilterParams>(() => ({
    dataset_id: datasetId!,
    captioned: captionedFilter,
    search: search || undefined,
    quality_flag: qualityFilter || undefined,
    score_filters: scoreFiltersParam,
    subfolder: activeSubfolder,
    source_video_id: frameVideoId || undefined,
    detection_label: detectionLabel || undefined,
    license_missing: licenseFilter === MISSING_LICENSE ? true : undefined,
    license_filter:
      licenseFilter && licenseFilter !== MISSING_LICENSE
        ? JSON.stringify([licenseFilter])
        : undefined,
  }), [datasetId, captionedFilter, search, qualityFilter, scoreFiltersParam, activeSubfolder, detectionLabel, licenseFilter, frameVideoId]);

  const imagesQueryKey = useMemo(
    () => ["images", datasetId, page, pageSize, sortOpt, captionedFilter, qualityFilter, search, scoreFiltersParam, activeSubfolder, detectionLabel, licenseFilter, frameVideoId],
    [datasetId, page, pageSize, sortOpt, captionedFilter, qualityFilter, search, scoreFiltersParam, activeSubfolder, detectionLabel, licenseFilter, frameVideoId]
  );

  const { data: images = [], isLoading, isPlaceholderData, refetch } = useQuery({
    queryKey: imagesQueryKey,
    queryFn: () =>
      imagesApi.list({
        ...filterParams,
        page,
        limit: pageSize,
        sort: sortOpt.sort,
        order: sortOpt.order,
      }),
    enabled: !!datasetId,
    placeholderData: keepPreviousData,
  });

  // How many images the filters match in total. The key nests under the
  // `["images", datasetId]` prefix every gallery mutation already invalidates
  // (TanStack matches by prefix), so a delete refreshes this the same way it
  // refreshes the grid — a sibling key like `["images-count", …]` would go stale
  // behind the user's back. No collision with the list key: its third element is
  // the page *number*, and `setQueryData` optimistic updates still address the
  // full list key. Paging and sort are absent on purpose — neither changes how
  // many images match.
  const { data: totalCount, isPlaceholderData: countIsStale } = useQuery({
    queryKey: ["images", datasetId, "count", captionedFilter, qualityFilter, search, scoreFiltersParam, activeSubfolder, detectionLabel, licenseFilter, frameVideoId],
    queryFn: () => imagesApi.count(filterParams).then((r) => r.count),
    enabled: !!datasetId,
    placeholderData: keepPreviousData,
  });

  imagesRef.current = images;

  // ── Select all matching filters ───────────────────────────────────────────
  // Whether *this page* is covered — which is a question about the visible ids,
  // never about `count`: the selection routinely runs past the page (the whole
  // filter set, or another subfolder's images gathered earlier), so comparing a
  // total against `images.length` would answer a different question.
  const pageAllSelected = images.length > 0 && images.every((i) => selectedIds.has(i.id));
  // The selection store is module-global, so in a split-pane setup it can hold
  // ids from another dataset — comparing its raw `count` to this view's total
  // would flip the row to "all selected" on someone else's selection.
  const selectedHere = useMemo(
    () => [...selectedIds].filter((id) => datasetByImageId.get(id) === datasetId).length,
    [selectedIds, datasetByImageId, datasetId]
  );
  // The row asserts *set identity* — that what is selected is exactly what the
  // filters match — and approximates it by cardinality. `===`, never `>=`:
  // filters do not clear the selection, so after taking the offer and then
  // narrowing to a subfolder the selection is a strict superset of the match
  // set, and `>=` would render "All 3 matching images selected" over a
  // selection of 8. Equality also reverts to the offer when a delete leaves
  // stale ids (9 selected vs 8 matching). Still an approximation: a
  // same-cardinality but different match set reads as complete. Every bulk
  // select being additive makes the superset case ordinary rather than rare —
  // gathering two subfolders in turn produces one — so the row offers again
  // instead of claiming, which is the honest branch of the two.
  const allMatchingSelected = totalCount !== undefined && totalCount > 0 && selectedHere === totalCount;
  const [selectingAll, setSelectingAll] = useState(false);

  const selectAllMatching = useCallback(() => {
    setSelectingAll(true);
    imagesApi
      .listIds({ ...filterParams, sort: sortOpt.sort, order: sortOpt.order })
      .then((r) => {
        selectMany(r.ids, datasetId ?? "");
        if (r.truncated) {
          toast(
            `Selected the first ${r.ids.length.toLocaleString()} of ${r.count.toLocaleString()} — ` +
            "too many to select at once",
            { icon: "⚠️" }
          );
        } else {
          toast.success(`Selected ${r.ids.length.toLocaleString()} image${r.ids.length !== 1 ? "s" : ""}`);
        }
      })
      .catch((err) => toast.error(apiErrorDetail(err, "Could not select all matching images")))
      .finally(() => setSelectingAll(false));
  }, [filterParams, sortOpt, selectMany, datasetId]);

  // The toolbar button's caret menu. Only worth showing when the filters match
  // more than one page — with everything on screen, "all matching" and "this
  // page" are the same click.
  const hasMoreThanPage = totalCount !== undefined && totalCount > images.length;
  const [selectMenuOpen, setSelectMenuOpen] = useState(false);
  const selectMenuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!selectMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (selectMenuRef.current && !selectMenuRef.current.contains(e.target as Node)) setSelectMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setSelectMenuOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [selectMenuOpen]);
  useEffect(() => { if (!hasMoreThanPage) setSelectMenuOpen(false); }, [hasMoreThanPage]);

  const totalPages = totalCount !== undefined ? Math.max(1, Math.ceil(totalCount / pageSize)) : undefined;
  // Reachable by deleting the tail of a dataset while parked on its last page.
  // Gated on the count being current: with `keepPreviousData` a pane that has
  // just switched datasets briefly holds the *previous* dataset's total, and
  // clamping against that would yank the user to an unrelated page.
  useEffect(() => {
    if (countIsStale || totalPages === undefined) return;
    if (page > totalPages) setPage(totalPages);
  }, [countIsStale, totalPages, page]);

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

  // Keyed on the `images` array *identity*, not its length: with `keepPreviousData`
  // `isLoading` stays false across a filter change, so a new result set that happens
  // to be the same length would not re-run this and a pending scroll-to-top would sit
  // armed until some unrelated later load consumed it.
  useEffect(() => {
    const el = scrollRef.current;
    if (isLoading || !el) return;
    if (pendingScrollTop.current) {
      pendingScrollTop.current = false;
      el.scrollTop = 0;
      return;
    }
    if (images.length > 0 && !hasRestoredScroll.current && saved?.scrollTop) {
      hasRestoredScroll.current = true;
      el.scrollTop = saved.scrollTop;
    }
  }, [images, isLoading, saved]);

  // What the detail view's ← / → step through. The whole `filterParams` memo goes
  // in — the same object the grid queries with — so the boundary prefetch cannot
  // ask for a page of a *different* result set (paging past the last image of a
  // subfolder used to land in the middle of the whole dataset).
  //
  // `ids` and `filters` must be written as one pair. With `keepPreviousData` the
  // old tiles stay on screen across a filter change while `images` keeps its
  // previous identity, so without the bail this fires immediately with ids from
  // filter set A and `filters` = B. Holding the old, self-consistent context until
  // the new page lands is the correct behaviour: it still describes what is on
  // screen.
  useEffect(() => {
    if (isPlaceholderData) return;
    if (images.length > 0 && datasetId) {
      writeNavContext(datasetId, {
        ids: images.map((i) => i.id),
        page,
        limit: pageSize,
        sort: sortOpt.sort,
        order: sortOpt.order,
        filters: filterParams,
      });
    }
  }, [images, isPlaceholderData, datasetId, page, pageSize, sortOpt, filterParams]);

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

  // One mutation for rename, the Move-to… picker and the drag — all three are the same
  // subtree prefix rewrite server-side, and all three need the same client-side
  // re-pointing afterwards. Deliberately *not* folded into moveToSubfolderMutation:
  // that one's clear() touches the module-global selection store, which must never
  // fire for a folder operation. See docs/dev/gallery.md § Renaming and moving a subfolder.
  const repathSubfolderMutation = useMutation({
    mutationFn: (p: { path: string; newPath: string; kind: "rename" | "move" }) =>
      datasetsApi.repathSubfolder(datasetId!, p.path, p.newPath),
    onSuccess: (data, vars) => {
      const from = data.previous_path;
      const to = data.path;
      const inSubtree = (p: string) => isInSubtree(p, from);
      const rewrite = (p: string) => to + p.slice(from.length);

      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      // The count key nests under this prefix, so pagination refreshes for free.
      qc.invalidateQueries({ queryKey: ["images", datasetId] });

      // Re-point, never clear: unlike a delete, the folder still exists. And no
      // resetPage() — the image set is identical, only its label changed.
      if (activeSubfolder !== undefined && inSubtree(activeSubfolder)) setActiveSubfolder(rewrite(activeSubfolder));
      // expandedPaths is a Set keyed by path, so a re-path orphans every key and
      // silently collapses the branch the user was working in. Rebuild it through the
      // same map, and add the destination's ancestors so a folder dropped into a
      // collapsed parent is revealed where it landed.
      setExpandedPaths(prev => {
        const next = new Set<string>();
        for (const p of prev) next.add(inSubtree(p) ? rewrite(p) : p);
        for (const p of ancestorPaths(to)) next.add(p);
        return next;
      });
      // The activeSubfolder effect covers the common case, but the user can override
      // the upload target independently — re-point it or the <select> renders blank.
      setUploadSubfolder(prev => (inSubtree(prev) ? rewrite(prev) : prev));
      setCreateChildOf(prev => (prev !== null && inSubtree(prev) ? null : prev));

      setRenamingPath(null);
      setPendingRepathSubfolder(null);
      const parent = to.includes("/") ? to.slice(0, to.lastIndexOf("/")) : "";
      toast.success(
        vars.kind === "rename"
          ? `Renamed to "${to}"`
          : `Moved "${to.split("/").pop()}" into "${parent || "(root)"}"`
      );
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not move the subfolder")),
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
  // A folder drag has no image behind it, so the overlay needs its own branch or it
  // renders nothing — and the "N images" badge must not appear for one either.
  const activeDragFolder = isSubfolderDragId(activeDragId) ? subfolderFromDragId(activeDragId) : null;
  const activeDragCount = activeDragId && !activeDragFolder ? dragIdsFor(activeDragId).length : 0;

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

  const handleDragStart = useCallback((e: DragStartEvent) => {
    setActiveDragId(String(e.active.id));
    setDropTargetPath(null);
  }, []);
  const handleDragCancel = useCallback(() => {
    setActiveDragId(null);
    setDropTargetPath(null);
  }, []);
  // Which folder row the pointer is currently over, for the overlay's drop-target chip.
  // `over` is already narrowed by `collisionDetection`, so this reads the decision rather
  // than re-deriving it — a row that lights up and a chip that names it can never disagree.
  const handleDragOver = useCallback((e: DragOverEvent) => {
    const id = e.over?.id;
    setDropTargetPath(id !== undefined && isSubfolderDropId(id) ? subfolderFromDropId(id) : null);
  }, []);

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event;
    setActiveDragId(null);
    setDropTargetPath(null);
    if (!over || !datasetId) return;

    // A dragged *folder* — must come before the branch below, which assumes active.id
    // is an image id. Dropping on the `(root)` row means "move to top level"; it is the
    // only un-nest gesture drag offers, since the sidebar background is the sentinel.
    if (isSubfolderDragId(active.id)) {
      if (!isSubfolderDropId(over.id)) return;
      const src = subfolderFromDragId(active.id);
      const destParent = subfolderFromDropId(over.id);
      if (!canDropFolderOn(src, destParent)) return;  // defence in depth
      const label = src.split("/").pop()!;
      const currentParent = src.includes("/") ? src.slice(0, src.lastIndexOf("/")) : "";
      if (destParent === currentParent) return;
      repathSubfolderMutation.mutate({
        path: src,
        newPath: destParent ? `${destParent}/${label}` : label,
        kind: "move",
      });
      return;
    }

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
  }, [qc, imagesQueryKey, datasetId, page, pageSize, reorderMutation, isCustomOrder, moveImagesTo, repathSubfolderMutation]);

  const handleUpload = useCallback(async (files: FileList | File[], sf?: string) => {
    if (!datasetId) return;
    const fileArray = Array.from(files);
    const subfolder = sf ?? uploadSubfolder;
    setUploadProgress({ datasetId, done: 0, total: fileArray.length, errors: 0, skipped: 0 });
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
      // Reported separately, never summed: a declined file is not a failure.
      setUploadProgress({ datasetId, done: i + 1, total: fileArray.length, errors, skipped: skippedSoFar });
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
      // Selecting it is also what reveals it: the activeSubfolder effect opens every
      // ancestor, so a child created inside a collapsed parent is visible where it landed.
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
      // The same re-pointing repathSubfolderMutation does, except the folder is gone, so
      // every path-keyed piece of state under it is cleared rather than rewritten.
      // `delete_subfolder` removes `path` *and* `path/%` plus every declared entry below,
      // so the whole subtree goes. Pruning expandedPaths matters now that the set is
      // persisted: dead keys would be stored forever, and a folder later re-created at the
      // same path would come back pre-expanded.
      if (activeSubfolder !== undefined && isInSubtree(activeSubfolder, path)) {
        setActiveSubfolder(undefined);
        resetPage();
      }
      setExpandedPaths(prev => withoutSubtree(prev, path));
      // `isInSubtree("", path)` is false, so the root upload target is never disturbed.
      setUploadSubfolder(prev => (isInSubtree(prev, path) ? "" : prev));
      setCreateChildOf(prev => (prev !== null && isInSubtree(prev, path) ? null : prev));
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
      const moved = pendingMoveSubfolder?.path;
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["subfolders", data.target_dataset_id] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
      qc.invalidateQueries({ queryKey: ["dataset", data.target_dataset_id] });
      // Deliberately prunes nothing from expandedPaths, unlike the delete above: the
      // backend matches `Image.subfolder == source_subfolder` exactly, one level, and
      // never touches declared_subfolders — move `alpha` and everything under
      // `alpha/inner` stays exactly where it was. Snapping the branch shut here would
      // close a folder that is still full, and persist that as the user's choice.
      if (moved !== undefined && activeSubfolder !== undefined && isInSubtree(activeSubfolder, moved)) { setActiveSubfolder(undefined); resetPage(); }
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

  const resetPage = () => { setPage(1); dropScrollRestore(); };

  const handleResetFilters = () => {
    if (datasetId) localStorage.removeItem(`gallery-state-${datasetId}`);
    setPage(1);
    setSortIdx(getGalleryDefaultSort());
    setCaptionedFilter(getGalleryDefaultCaptionFilter());
    setQualityFilter(getGalleryDefaultQualityFilter() as QualityFilter);
    setLicenseFilter("");
    setActiveSubfolder(undefined);
    setFrameVideoId(undefined);
    // `expandedPaths` is deliberately not reset. It rides in the same blob the line above
    // removes, but it is the tree's shape, not a filter — collapsing everything is not
    // what "reset filters" promises. The next persist writes the live set back.
    dropScrollRestore();
    // Immediate, unlike the deep-link paths: this is a direct gesture and wants
    // feedback now, not once the new page renders.
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

  // Enter commits a rename. `name` is already separator-free (stripped on input) and
  // trimmed; the no-op cases are dropped here rather than sent, which is what makes the
  // endpoint's empty/same-path/'..' 400 branches unreachable from the UI.
  const commitRename = (node: SubfolderNode) => {
    const name = renameDraft.trim();
    if (!name || name === node.label || name === "." || name === "..") { setRenamingPath(null); return; }
    const parent = node.path.includes("/") ? node.path.slice(0, node.path.lastIndexOf("/")) : "";
    repathSubfolderMutation.mutate({
      path: node.path,
      newPath: parent ? `${parent}/${name}` : name,
      kind: "rename",
    });
  };

  // Most-used first, destructive last. Four of the six call the *same* setters the hover
  // buttons already call — the menu is additive, nothing was relocated into it. Rename
  // and Move have no button counterpart because a 180 px row already holds four; that is
  // deliberate, not an unfinished set.
  const folderMenuActions = (node: SubfolderNode): ContextMenuAction[] => [
    {
      label: "New subfolder inside…",
      icon: <Plus size={13} />,
      onClick: () => { setCreateChildOf(node.path); setNewChildName(""); },
    },
    {
      label: "Rename…",
      icon: <Edit2 size={13} />,
      onClick: () => { setRenamingPath(node.path); setRenameDraft(node.label); },
    },
    {
      label: "Move to…",
      icon: <FolderInput size={13} />,
      onClick: () => setPendingRepathSubfolder(node),
    },
    {
      label: "Move to another dataset…",
      icon: <ArrowRightFromLine size={13} />,
      onClick: () => setPendingMoveSubfolder(node),
    },
    {
      label: "Copy to another dataset…",
      icon: <Copy size={13} />,
      onClick: () => setPendingCopySubfolder(node),
    },
    {
      label: "Delete",
      icon: <Trash2 size={13} />,
      onClick: () => setPendingDeleteSubfolder(node),
      danger: true,
    },
  ];

  const renderSubfolderNode = (node: SubfolderNode) => {
    const isExpanded = expandedPaths.has(node.path);
    const isActive = activeSubfolder === node.path;
    const hasChildren = node.children.length > 0;
    const isRenaming = renamingPath === node.path;

    return (
      <div key={node.path}>
        <SubfolderRowDnd dropId={subfolderDropId(node.path)} dragId={subfolderDragId(node.path)}>
        {({ setNodeRef, setActivatorNodeRef, listeners, attributes, isOver, isDragging }) => (
        <div
          ref={setNodeRef}
          className="subfolder-row"
          // A right-press never starts a drag: PointerSensor.activators bails on
          // event.button !== 0, so the menu and the draggable coexist untouched.
          onContextMenu={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setFolderMenu({ x: e.clientX, y: e.clientY, node });
          }}
          style={{
            display: "flex", alignItems: "center",
            borderRadius: "var(--r)",
            // A drop target must not look like the selected row: both used to be the
            // same neutral `--surface-3`, so the only cue that this row was the target
            // was a 1px ring, on a row the drag preview was covering.
            background: isOver ? "var(--accent-glow)" : isActive ? "var(--surface-3)" : "transparent",
            boxShadow: isOver ? "inset 0 0 0 2px var(--accent)" : "none",
            opacity: isDragging ? 0.4 : 1,
          }}
        >
          {/* expand/collapse toggle (doubles as the row's indent). Its glyph is the whole of
              its content, so without a label it is a nameless button to a screen reader — and
              to a test looking for a stable handle on it. A leaf has nothing to expand, so it
              renders the same box as an inert spacer rather than an empty, focusable, no-op
              button; the box is shared through toggleCellStyle so the two cannot drift. */}
          {hasChildren ? (
            <button
              type="button"
              aria-label={`${isExpanded ? "Collapse" : "Expand"} ${node.path}`}
              aria-expanded={isExpanded}
              onClick={(e) => {
                e.stopPropagation();
                setExpandedPaths(prev => {
                  const next = new Set(prev);
                  if (next.has(node.path)) next.delete(node.path); else next.add(node.path);
                  return next;
                });
              }}
              style={{ ...toggleCellStyle(node.depth), cursor: "pointer" }}
            >{isExpanded ? "▼" : "▶"}</button>
          ) : (
            <span aria-hidden="true" style={toggleCellStyle(node.depth)} />
          )}

          {isRenaming ? (
            /* Inline, not a modal: the in-row input keeps the tree indentation visible,
               so it is obvious which folder is being renamed. Single-segment by
               construction — separators are stripped on input rather than rejected on
               submit, so typing "a/b" yields "ab" and this control can never *move* a
               folder by accident. The four hover buttons are hidden while it is open
               (× sits one pixel from Enter). */
            <input
              className="input"
              style={{ flex: 1, minWidth: 0, fontSize: 12, padding: "3px 6px", marginRight: 4 }}
              value={renameDraft}
              autoFocus
              onFocus={(e) => e.currentTarget.select()}
              onChange={(e) => setRenameDraft(e.target.value.replace(/[\\/]/g, ""))}
              onBlur={() => setRenamingPath(null)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitRename(node);
                if (e.key === "Escape") {
                  // GalleryPage has a document-level Escape handler of its own.
                  e.stopPropagation();
                  setRenamingPath(null);
                }
              }}
            />
          ) : (
          <>
          {/* label + count — also the drag activator. Listeners go *here*, not on the
              row: the hover buttons below are siblings of the activator, so a pointerdown
              on × never reaches them, and PointerSensor.activators has no
              interactive-element filter. onClick still fires thanks to the 8 px
              activationConstraint, the same contract the image cards rely on. */}
          <button
            ref={setActivatorNodeRef}
            {...listeners}
            {...attributes}
            // Opening the folder is done here as well as in the activeSubfolder effect:
            // re-clicking the row you are already standing in sets the same value, which
            // React bails out of, so the effect never fires and a collapsed active folder
            // would stay shut with no way to open it but the ▶ toggle.
            onClick={() => {
              setActiveSubfolder(node.path);
              setExpandedPaths(prev => (prev.has(node.path) ? prev : new Set(prev).add(node.path)));
              resetPage();
            }}
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
          </>
          )}
        </div>
        )}
        </SubfolderRowDnd>

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
            {(dataset?.video_count ?? 0) > 0 && (
              <span className="badge dot">
                {dataset?.video_count} {dataset?.video_count === 1 ? "video" : "videos"}
              </span>
            )}
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

        {/* Frame lineage. Rendered only when the dataset actually has videos —
            the same "look untouched for image-only datasets" rule VideoStrip
            follows. Unlike the subfolder sidebar this survives curation: the
            lineage column does not move when a frame is renamed or re-filed. */}
        {videos && videos.length > 0 && (
          <select className="select" style={{ width: "auto", maxWidth: 220 }} value={frameVideoId ?? ""}
            aria-label="Filter by source video"
            title={videos.find((v) => v.id === frameVideoId)?.filename ?? "All images"}
            onChange={(e) => { setFrameVideoId(e.target.value || undefined); resetPage(); }}>
            <option value="">All images</option>
            {videos.map((v) => (
              <option key={v.id} value={v.id}>Frames from {v.filename}</option>
            ))}
          </select>
        )}

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

        {/* "Select all" means every image the filters match — the dataset, or the
            subfolder if one is active — not the page that happens to be on screen.
            Page-only lives in the caret menu, because wanting exactly the visible
            50 is the rarer intent and the one that has an alternative (drag or
            shift-click). Both are additive: see `selectMany` in the store. */}
        <div ref={selectMenuRef} style={{ position: "relative", display: "flex" }}>
          <button
            className="btn ghost sm"
            data-testid="select-all-btn"
            disabled={selectingAll || images.length === 0}
            title={pageAllSelected
              ? "Deselect every image selected in this dataset"
              : hasMoreThanPage
                ? `Select all ${totalCount!.toLocaleString()} images matching the current filters`
                : "Select every image matching the current filters"}
            onClick={() => pageAllSelected ? clearDataset(datasetId ?? "") : selectAllMatching()}
            style={hasMoreThanPage ? { borderTopRightRadius: 0, borderBottomRightRadius: 0 } : undefined}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/>
            </svg>
            {selectingAll ? "Selecting…" : pageAllSelected ? "Deselect all" : "Select all"}
          </button>
          {hasMoreThanPage && (
            <button
              className="btn ghost sm"
              data-testid="select-all-menu-btn"
              aria-label="Select all options"
              aria-expanded={selectMenuOpen}
              onClick={() => setSelectMenuOpen((o) => !o)}
              style={{ padding: "0 5px", marginLeft: -1, borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
            >
              <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 6l5 5 5-5"/>
              </svg>
            </button>
          )}
          {selectMenuOpen && (
            <div
              data-testid="select-all-menu"
              style={{
                position: "absolute", top: "calc(100% + 4px)", right: 0, zIndex: 1000,
                background: "var(--surface-2)", border: "1px solid var(--line-2)",
                borderRadius: "var(--r)", boxShadow: "0 8px 24px rgba(0,0,0,.4)",
                minWidth: 210, padding: "4px 0", whiteSpace: "nowrap",
              }}
            >
              {[
                {
                  label: `All ${totalCount?.toLocaleString()} matching filters`,
                  onClick: selectAllMatching,
                },
                pageAllSelected
                  ? { label: `Deselect this page (${images.length})`, onClick: () => deselectMany(images.map(i => i.id)) }
                  : { label: `This page only (${images.length})`, onClick: () => selectMany(images.map(i => i.id), datasetId ?? "") },
              ].map((a) => (
                <button
                  key={a.label}
                  onClick={() => { a.onClick(); setSelectMenuOpen(false); }}
                  style={{
                    display: "block", width: "100%", padding: "7px 14px", fontSize: 13,
                    background: "none", border: "none", color: "var(--fg)",
                    cursor: "pointer", textAlign: "left",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-3)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                >
                  {a.label}
                </button>
              ))}
            </div>
          )}
        </div>

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
          {/* The prefix is not decoration: this select is the Upload button's
              destination and changes nothing until a file is uploaded, so beside
              the selection controls a bare "(root)" reads as if it scopes them. */}
          {subfolders.length > 0 && (
            <span style={{ fontSize: 12, color: "var(--fg-mute)", whiteSpace: "nowrap" }}>Upload to:</span>
          )}
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
              {/* "files", not "images": videos upload through this bar too, and the
                  split between them is not known until the responses land — which is
                  what the summary toast reports. Same vocabulary as that toast. */}
              {uploadProgress.done} / {uploadProgress.total} files
              {uploadProgress.errors > 0 && ` · ${uploadProgress.errors} failed`}
              {uploadProgress.skipped > 0 && (
                <span style={{ color: "var(--fg-mute)" }}> · {uploadProgress.skipped} skipped</span>
              )}
            </span>
          </div>
        </div>
      )}

      {/* Source videos. Outside the DndContext below on purpose: inside it the
          cards would join the grid's collision detection and its subfolder drop
          targets, and inside the grid's scroll container they would also sit
          under the drag-to-upload handler. Renders nothing without videos. */}
      <VideoStrip datasetId={datasetId} />

      {/* Grid area with subfolder sidebar. One DndContext spans both so image cards
          can be dragged from the grid onto a subfolder row. */}
      <DndContext
        sensors={sensors}
        collisionDetection={collisionDetection}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
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
                  background: isOver ? "var(--accent-glow)" : isRootActive ? "var(--surface-3)" : "transparent",
                  boxShadow: isOver ? "inset 0 0 0 2px var(--accent)" : "none",
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
        {/* Select-all-matching offer. Appears once the whole page is selected and
            there is more behind the filters than fits on it — Gmail's pattern.
            The toolbar button reaches the whole set directly now, so this row is
            the follow-up for the paths that select a page: "This page only" from
            the caret menu, a drag, a shift-click range, or checking every tile. */}
        {pageAllSelected && totalCount !== undefined && totalCount > images.length && (
          <div
            data-testid="select-all-matching"
            style={{
              display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
              flexWrap: "wrap", marginBottom: 12, padding: "7px 12px",
              borderRadius: "var(--r-md)", border: "1px solid var(--line)",
              background: "var(--surface-1)", fontSize: 12, color: "var(--fg-mute)",
            }}
          >
            {allMatchingSelected ? (
              <>
                <span>All {totalCount.toLocaleString()} matching images selected —</span>
                {/* This dataset's ids only: the store is module-global, so a bare
                    `clear()` here would empty the other pane's selection too. */}
                <button className="link-btn" onClick={() => clearDataset(datasetId ?? "")}>Clear selection</button>
              </>
            ) : (
              <>
                <span>All {images.length.toLocaleString()} on this page selected —</span>
                <button className="link-btn" disabled={selectingAll} onClick={selectAllMatching}>
                  {selectingAll
                    ? "Selecting…"
                    : `Select all ${totalCount.toLocaleString()} matching filters`}
                </button>
              </>
            )}
          </div>
        )}

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

        {/* Pagination. `totalPages` comes from the count query; the old
            "a full page means there is probably another" heuristic stays as the
            fallback for the first paint, so the row does not flicker in. */}
        {(() => {
          const hasNext = totalPages !== undefined ? page < totalPages : images.length === pageSize;
          if (page <= 1 && !hasNext) return null;
          return (
            <div style={{ display: "flex", justifyContent: "center", gap: 12, marginTop: 24 }}>
              {page > 1 && <button className="btn" onClick={() => setPage(p => p - 1)}>← Previous</button>}
              <span style={{ alignSelf: "center", fontSize: 12, color: "var(--fg-mute)" }}>
                {totalPages !== undefined && totalCount !== undefined
                  ? `Page ${page} of ${totalPages} · ${totalCount.toLocaleString()} image${totalCount !== 1 ? "s" : ""}`
                  : `Page ${page}`}
              </span>
              {hasNext && <button className="btn" onClick={() => setPage(p => p + 1)}>Next →</button>}
            </div>
          );
        })()}

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
            <div style={{ position: "relative", pointerEvents: "none" }}>
              {/* The card is card-sized and the sidebar is 180px wide, so over a folder
                  row the preview sits squarely on top of the row it is about to drop
                  into — the highlight underneath was unreadable, which is the whole
                  complaint. Fade the picture out of the way and let the row show
                  through; the chip below then says where it lands. */}
              <div style={{
                opacity: dropTargetPath !== null ? 0.25 : 0.92,
                transition: "opacity .12s",
                boxShadow: "0 12px 32px -8px rgba(0,0,0,.6)", borderRadius: "var(--r-lg)",
              }}>
                <ImageCard image={activeDragImage} />
              </div>
              {/* Outside the faded wrapper: the count is the other thing you need to read
                  at the moment of dropping. */}
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
              {dropTargetPath !== null && (
                <span style={{
                  position: "absolute", left: "50%", top: "50%", zIndex: 2,
                  transform: "translate(-50%, -50%)", maxWidth: "94%",
                  display: "flex", alignItems: "center", gap: 6,
                  padding: "6px 11px", borderRadius: 999,
                  background: "var(--accent)", color: "#03130d",
                  fontSize: 12, fontWeight: 600,
                  boxShadow: "0 6px 18px -6px rgba(0,0,0,.7)",
                }}>
                  <FolderInput size={12} style={{ flexShrink: 0 }} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {dropTargetPath === "" ? "(root)" : dropTargetPath}
                  </span>
                </span>
              )}
            </div>
          )}
          {activeDragFolder && (
            <div style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "5px 10px", borderRadius: "var(--r)",
              background: "var(--surface-3)", border: "1px solid var(--accent)",
              boxShadow: "0 8px 24px -8px rgba(0,0,0,.6)",
              color: "var(--fg)", fontSize: 12.5, fontWeight: 600,
              pointerEvents: "none", opacity: 0.95, maxWidth: 220,
            }}>
              <Folder size={13} style={{ flexShrink: 0, opacity: 0.8 }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {activeDragFolder.split("/").pop()}
              </span>
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

      {/* Subfolder row context menu. "All" is the no-filter state rather than a folder,
          and `(root)` has no path to rename and its actions are a pixel away already —
          so neither row gets one. */}
      {folderMenu && (
        <ContextMenu
          x={folderMenu.x}
          y={folderMenu.y}
          actions={folderMenuActions(folderMenu.node)}
          onClose={() => setFolderMenu(null)}
        />
      )}

      {pendingRepathSubfolder && (
        <MoveSubfolderModal
          node={pendingRepathSubfolder}
          subfolders={subfolders}
          isPending={repathSubfolderMutation.isPending}
          onConfirm={(newPath) => repathSubfolderMutation.mutate({ path: pendingRepathSubfolder.path, newPath, kind: "move" })}
          onClose={() => setPendingRepathSubfolder(null)}
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
