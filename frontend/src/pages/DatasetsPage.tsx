import { useState, useMemo, useEffect, useCallback, useRef } from "react";
import type { HTMLAttributes, DragEvent as ReactDragEvent } from "react";
import { DECLARED_CATEGORIES_KEY, DATASETS_UI_KEY } from "../constants/storage";
import { loadPersisted } from "../utils/persistentState";
import { useDebouncedPersist } from "../hooks/useDebouncedPersist";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { usePaneNavigate } from "../hooks/usePaneNavigate";
import { useCustomLicenses } from "../hooks/useCustomLicenses";
import toast from "react-hot-toast";
import { datasetsApi, type DatasetProvenance } from "../api/datasets";
import ProvenanceFields from "../components/common/ProvenanceFields";
import SharedLicenseBadge from "../components/common/LicenseBadge";
import { EMPTY_PROVENANCE } from "../constants/licenses";
import { invalidateProvenanceScope } from "../constants/queryKeys";
import { licenseInfo } from "../constants/licenses";
import { imagesApi } from "../api/images";
import { jobsApi } from "../api/jobs";
import { apiErrorDetail } from "../utils/apiError";
import { showImportSummaryToast } from "../utils/importToast";
import { showUploadSummaryToast, tallyUpload } from "../utils/uploadToast";
import { versioningApi } from "../api/versioning";
import { settingsApi } from "../api/settings";
import type { Dataset } from "../types";
import ConfirmDialog from "../components/common/ConfirmDialog";
import ImportFolderModal from "../components/common/ImportFolderModal";
import DirPickerModal from "../components/common/DirPickerModal";
import { useJobStore } from "../store/jobStore";
import { invalidateDatasetContentScope } from "../constants/queryKeys";

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  return `${(bytes / 1_073_741_824).toFixed(2)} GB`;
}

/**
 * Dataset-level license badge — the default that images inherit when their own
 * license is unset. Unlike the per-image gallery badge (opt-in, hidden when
 * empty) this always renders: there is one per card, and "No license" is itself
 * the state a user needs to spot at a glance.
 */
function LicenseBadge({ ds }: { ds: Dataset }) {
  const info = licenseInfo(ds.license);
  const title = [
    ds.license ? `Default license: ${info.label}` : "No default license set",
    ds.source_name ? `Source: ${ds.source_name}` : null,
    info.allowsCommercial === false ? "Non-commercial only" : null,
    info.requiresAttribution ? "Attribution required" : null,
  ].filter(Boolean).join(" — ");
  return <SharedLicenseBadge value={ds.license} title={title} className="shrink-0" />;
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/* Deterministic placeholder tile gradient per card index */
const TILE_HUES = [
  ["#1f3a32","#10221c","#274d40"],
  ["#2a2520","#181412","#3a3128"],
  ["#1f3142","#0f1e2a","#2a4259"],
  ["#3a3a3a","#222222","#4a4a4a"],
  ["#3d2f24","#22180f","#503a2c"],
  ["#2a2440","#16122a","#3b3358"],
  ["#202020","#0f0f0f","#2a2a2a"],
];
function tileGrad(dsIndex: number, k: number) {
  const h = TILE_HUES[(dsIndex + k) % TILE_HUES.length];
  const angle = 135 + k * 7;
  return `linear-gradient(${angle}deg, ${h[0]}, ${h[1]} 60%, ${h[2]})`;
}

const SORT_OPTIONS = [
  { value: "created_desc", label: "Newest" },
  { value: "created_asc",  label: "Oldest" },
  { value: "updated_desc", label: "Recently updated" },
  { value: "name_asc",     label: "Name A → Z" },
  { value: "name_desc",    label: "Name Z → A" },
  { value: "images_desc",  label: "Most images" },
  { value: "images_asc",   label: "Fewest images" },
  { value: "size_desc",    label: "Largest" },
  { value: "size_asc",     label: "Smallest" },
  { value: "captioned_desc", label: "Most captioned %" },
];

/** Section key for datasets with no category. Not a real category name. */
const UNCATEGORIZED = "(Uncategorized)";

type Density = "grid" | "rows";

/** Persisted page UI state (DATASETS_UI_KEY). `selectedCategory: null` = "All". */
interface DatasetsUiConfig {
  collapsed: string[];
  density: Density;
  selectedCategory: string | null;
}
const DATASETS_UI_DEFAULTS: DatasetsUiConfig = {
  collapsed: [],
  density: "grid",
  selectedCategory: null,
};

// ── CategoryPicker ────────────────────────────────────────────────────────────
// Select from existing categories or type a new one.
interface CategoryPickerProps {
  value: string;
  onChange: (v: string) => void;
  existingCategories: string[];
  label?: string;
  labelNote?: string;
  autoFocusNew?: boolean;
}
function CategoryPicker({ value, onChange, existingCategories, label, labelNote, autoFocusNew }: CategoryPickerProps) {
  // "new" mode: user explicitly chose "New category…" from the dropdown.
  // CategoryPicker is always inside a conditionally-rendered modal, so it remounts
  // on each open — no need for a sync effect; useState init is sufficient.
  const inExisting = value === "" || existingCategories.includes(value);
  const [isNew, setIsNew] = useState(!inExisting);

  const selectValue = isNew ? "__new__" : value;

  const handleSelect = (v: string) => {
    if (v === "__new__") {
      setIsNew(true);
      onChange(""); // clear; user will type
    } else {
      setIsNew(false);
      onChange(v);
    }
  };

  return (
    <div>
      {label && (
        <label className="label">
          {label}
          {labelNote && <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}> {labelNote}</span>}
        </label>
      )}
      <select
        className="select"
        value={selectValue}
        onChange={(e) => handleSelect(e.target.value)}
        style={{ width: "100%" }}
      >
        <option value="">(None)</option>
        {existingCategories.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
        <option value="__new__">New category…</option>
      </select>
      {isNew && (
        <input
          className="input"
          placeholder="Category name"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus={autoFocusNew}
          style={{ marginTop: 8, width: "100%" }}
        />
      )}
    </div>
  );
}

export default function DatasetsPage() {
  const { go } = usePaneNavigate();
  const qc = useQueryClient();

  // ── Search & Sort ────────────────────────────────────────────────────────
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("created_desc");

  // ── Persisted page UI (collapse / density / rail selection) ──────────────
  // Coerce every field: loadPersisted shallow-merges arbitrary parsed JSON, so a
  // hand-edited or corrupted blob can hand back the wrong type.
  const [persistedUi] = useState(() => loadPersisted(DATASETS_UI_KEY, DATASETS_UI_DEFAULTS));
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(
    () => new Set(Array.isArray(persistedUi.collapsed) ? persistedUi.collapsed.filter((c) => typeof c === "string") : []),
  );
  const [density, setDensity] = useState<Density>(persistedUi.density === "rows" ? "rows" : "grid");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(
    typeof persistedUi.selectedCategory === "string" ? persistedUi.selectedCategory : null,
  );

  // Root element — owns the native file-drop listeners and hosts the highlight lookup.
  const pageRef = useRef<HTMLDivElement>(null);

  // ── Newly created dataset: scroll into view + flash ──────────────────────
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Category management (rename/delete) ──────────────────────────────────
  const [renamingCategory, setRenamingCategory] = useState<string | null>(null);
  const [renameCategoryValue, setRenameCategoryValue] = useState("");
  const [deletingCategory, setDeletingCategory] = useState<string | null>(null);

  // ── Empty categories (localStorage-backed so they survive page reloads) ──
  const [emptyCategories, setEmptyCategories] = useState<string[]>(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem(DECLARED_CATEGORIES_KEY) || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch { return []; }
  });

  // ── New Category modal ────────────────────────────────────────────────────
  const [showCreateCategory, setShowCreateCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState("");

  // ── Create modal ─────────────────────────────────────────────────────────
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [newProvenance, setNewProvenance] = useState<DatasetProvenance>(EMPTY_PROVENANCE);

  // ── Edit modal ───────────────────────────────────────────────────────────
  const [renameTarget, setRenameTarget] = useState<Dataset | null>(null);
  const [renameName, setRenameName] = useState("");
  const [renameDesc, setRenameDesc] = useState("");
  const [renameCategory, setRenameCategory] = useState("");
  const [renameProvenance, setRenameProvenance] = useState<DatasetProvenance>(EMPTY_PROVENANCE);

  // ── Delete modal ─────────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = useState<Dataset | null>(null);

  // ── Import modal ─────────────────────────────────────────────────────────
  const [importOpen, setImportOpen] = useState(false);
  const [importInitialId, setImportInitialId] = useState<string | undefined>(undefined);
  const [importJobId, setImportJobId] = useState<string | null>(null);
  const importJobProgress = useJobStore((s) => s.activeJobs.get(importJobId ?? ""));

  // ── Rescan + caption-import jobs ──────────────────────────────────────────
  const [rescanJobId, setRescanJobId] = useState<string | null>(null);
  const [rescanTargetId, setRescanTargetId] = useState<string | null>(null);
  const rescanJobProgress = useJobStore((s) => s.activeJobs.get(rescanJobId ?? ""));

  // ── Import-captions modal ─────────────────────────────────────────────────
  const [captionImportTarget, setCaptionImportTarget] = useState<Dataset | null>(null);
  const [captionImportPath, setCaptionImportPath] = useState("");
  const [captionDirPickerOpen, setCaptionDirPickerOpen] = useState(false);
  const [captionJobId, setCaptionJobId] = useState<string | null>(null);
  const captionJobProgress = useJobStore((s) => s.activeJobs.get(captionJobId ?? ""));

  // ── Duplicate modal ──────────────────────────────────────────────────────
  const [duplicateTarget, setDuplicateTarget] = useState<Dataset | null>(null);
  const [duplicateName, setDuplicateName] = useState("");
  const [duplicateVersionId, setDuplicateVersionId] = useState<string | undefined>(undefined);
  const [duplicateJobId, setDuplicateJobId] = useState<string | null>(null);
  const [dupBranchId, setDupBranchId] = useState<string | undefined>(undefined);
  // Off by default, matching the backend: doubling the footage is a costed
  // choice, not something a duplicate does quietly.
  const [duplicateIncludeVideos, setDuplicateIncludeVideos] = useState(false);
  const duplicateJobProgress = useJobStore((s) => s.activeJobs.get(duplicateJobId ?? ""));

  // ── Drag/drop ────────────────────────────────────────────────────────────
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const dragTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Dataset-to-category drag state
  const [draggingDatasetId, setDraggingDatasetId] = useState<string | null>(null);
  const [dropTargetCategory, setDropTargetCategory] = useState<string | null>(null);
  const [railDropTarget, setRailDropTarget] = useState<string | null>(null);

  // ── Queries ──────────────────────────────────────────────────────────────
  const { data: datasets = [], isLoading } = useQuery({
    queryKey: ["datasets"],
    queryFn: datasetsApi.list,
    staleTime: 0,
  });

  const { data: thresholds } = useQuery({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });
  const versioningEnabled = thresholds?.versioning_mode !== "off";

  const { data: dupBranches = [] } = useQuery({
    queryKey: ["branches", duplicateTarget?.id],
    queryFn: () => versioningApi.listBranches(duplicateTarget!.id),
    enabled: !!duplicateTarget && versioningEnabled,
  });

  const resolvedDupBranchId = dupBranchId ?? duplicateTarget?.current_branch_id ?? dupBranches[0]?.id;

  const { data: dupVersions = [] } = useQuery({
    queryKey: ["versions", duplicateTarget?.id, resolvedDupBranchId, "dup"],
    queryFn: () => versioningApi.listVersions(duplicateTarget!.id, { branchId: resolvedDupBranchId }),
    enabled: !!duplicateTarget && versioningEnabled && !!resolvedDupBranchId,
  });

  // ── Derived data ─────────────────────────────────────────────────────────
  const filteredAndSorted = useMemo(() => {
    const f = datasets.filter((d) => d.name.toLowerCase().includes(search.toLowerCase()));
    return [...f].sort((a, b) => {
      switch (sortBy) {
        case "name_asc":      return a.name.localeCompare(b.name);
        case "name_desc":     return b.name.localeCompare(a.name);
        case "created_asc":   return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
        case "created_desc":  return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        case "updated_desc":  return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
        case "images_desc":   return b.image_count - a.image_count;
        case "images_asc":    return a.image_count - b.image_count;
        case "size_desc":     return b.total_size_bytes - a.total_size_bytes;
        case "size_asc":      return a.total_size_bytes - b.total_size_bytes;
        case "captioned_desc": {
          const pa = a.image_count ? a.captioned_count / a.image_count : 0;
          const pb = b.image_count ? b.captioned_count / b.image_count : 0;
          return pb - pa;
        }
        default: return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      }
    });
  }, [datasets, search, sortBy]);

  const existingCategories = useMemo(
    () => [...new Set([...datasets.map((d) => d.category).filter(Boolean), ...emptyCategories])].sort() as string[],
    [datasets, emptyCategories]
  );

  const totalImages = datasets.reduce((s, d) => s + d.image_count, 0);
  const totalSize = datasets.reduce((s, d) => s + d.total_size_bytes, 0);

  // ── Effects ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!importJobId || !importJobProgress) return;
    if (importJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      if (importJobProgress.dataset_id) {
        qc.invalidateQueries({ queryKey: ["images", importJobProgress.dataset_id] });
        qc.invalidateQueries({ queryKey: ["dataset", importJobProgress.dataset_id] });
        qc.invalidateQueries({ queryKey: ["dataset-stats", importJobProgress.dataset_id] });
        qc.invalidateQueries({ queryKey: ["tag-stats", importJobProgress.dataset_id] });
        qc.invalidateQueries({ queryKey: ["score-values", importJobProgress.dataset_id] });
        qc.invalidateQueries({ queryKey: ["tag-cooccurrence", importJobProgress.dataset_id] });
        // An import is a provenance writer: a scraper sidecar is the largest
        // source of new `other:` licenses, unpickable until this refetches.
        qc.invalidateQueries({ queryKey: ["licenses-in-use", importJobProgress.dataset_id] });
      }
      showImportSummaryToast(importJobId);
      setImportJobId(null);
    } else if (importJobProgress.status === "failed") {
      setImportJobId(null);
    }
  }, [importJobProgress?.status, importJobId, qc]);

  const invalidateDatasetCaches = useCallback((datasetId: string) => {
    invalidateDatasetContentScope(qc, datasetId);
    // Both callers (rescan and import) can add Video rows now, and the preview
    // strip's counts come off `["dataset", id]` — so leaving this out desynced
    // the card's video count from the video list any pane opened next.
    qc.invalidateQueries({ queryKey: ["videos", datasetId] });
  }, [qc]);

  useEffect(() => {
    if (!rescanJobId || !rescanJobProgress) return;
    if (rescanJobProgress.status === "completed") {
      const dsId = rescanJobProgress.dataset_id;
      if (dsId) invalidateDatasetCaches(dsId);
      jobsApi.get(rescanJobId).then((job) => {
        const r = job.result_data as {
          added?: number; renamed?: number; captions_updated?: number; missing?: unknown[];
          videos_added?: number; videos_missing?: unknown[];
        };
        const added = r.added ?? 0;
        const captions = r.captions_updated ?? 0;
        const missing = (r.missing ?? []).length + (r.videos_missing ?? []).length;
        toast.success(
          `Rescan complete — ${added} added, ${captions} caption(s) updated` +
          (r.videos_added ? `, ${r.videos_added} video(s) added` : "") +
          // Kept in step with GalleryPage's rescan toast: rescan otherwise never
          // touches a file, so a rename has to be reported rather than inferred.
          (r.renamed ? `, ${r.renamed} renamed to avoid a name clash` : "") +
          (missing ? `, ${missing} missing on disk` : "")
        );
      }).catch(() => toast.success("Rescan complete"));
      setRescanJobId(null);
      setRescanTargetId(null);
    } else if (rescanJobProgress.status === "failed") {
      toast.error("Rescan failed");
      setRescanJobId(null);
      setRescanTargetId(null);
    }
  }, [rescanJobProgress?.status, rescanJobId, invalidateDatasetCaches]);

  useEffect(() => {
    if (!captionJobId || !captionJobProgress) return;
    if (captionJobProgress.status === "completed") {
      const dsId = captionJobProgress.dataset_id;
      if (dsId) invalidateDatasetCaches(dsId);
      jobsApi.get(captionJobId).then((job) => {
        const r = job.result_data as { matched?: number; unmatched?: unknown[] };
        const matched = r.matched ?? 0;
        const unmatched = (r.unmatched ?? []).length;
        toast.success(
          `Captions imported — ${matched} matched` + (unmatched ? `, ${unmatched} unmatched` : "")
        );
      }).catch(() => toast.success("Captions imported"));
      setCaptionJobId(null);
    } else if (captionJobProgress.status === "failed") {
      toast.error("Caption import failed");
      setCaptionJobId(null);
    }
  }, [captionJobProgress?.status, captionJobId, invalidateDatasetCaches]);

  useEffect(() => {
    if (!duplicateJobId || !duplicateJobProgress) return;
    if (duplicateJobProgress.status === "completed") {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      const jobId = duplicateJobId;
      jobsApi.get(jobId).then((job) => {
        const r = (job.result_data ?? {}) as {
          images_added?: number; videos_added?: number; videos_failed?: number;
        };
        // The video clauses drop out at zero, so an image-only duplicate reads
        // exactly as it did before the toggle existed.
        toast.success(
          `Dataset duplicated — ${r.images_added ?? 0} image(s)` +
          (r.videos_added ? `, ${r.videos_added} video(s)` : "") +
          (r.videos_failed ? `, ${r.videos_failed} video(s) failed to copy` : "")
        );
      }).catch(() => toast.success("Dataset duplicated"));
      setDuplicateJobId(null);
    } else if (duplicateJobProgress.status === "failed") {
      setDuplicateJobId(null);
      toast.error("Duplicate failed");
    }
  }, [duplicateJobProgress?.status, duplicateJobId, qc]);

  useEffect(() => {
    localStorage.setItem(DECLARED_CATEGORIES_KEY, JSON.stringify(emptyCategories));
  }, [emptyCategories]);

  // Persist page UI state. `collapsed` is deliberately NOT pruned to currently-known
  // categories — that would discard collapse state for categories hidden by search.
  useDebouncedPersist(DATASETS_UI_KEY, {
    collapsed: [...collapsedCategories],
    density,
    selectedCategory,
  });

  // Scroll a newly created dataset into view once the refetched list has rendered it.
  // Re-runs on `datasets` because the node does not exist until the invalidation lands.
  // No cleanup that clears the timer: `datasets` churns on every refetch and would keep
  // resetting the 2s window. The unmount cleanup + hard expiry below cover it.
  useEffect(() => {
    if (!highlightId) return;
    const el = pageRef.current?.querySelector<HTMLElement>(`[data-dataset-id="${CSS.escape(highlightId)}"]`);
    if (!el) return;
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    highlightTimerRef.current = setTimeout(() => setHighlightId(null), 2000);
  }, [highlightId, datasets]);

  // Hard expiry so an id that never renders (filtered out, deleted) can't pin the class.
  useEffect(() => {
    if (!highlightId) return;
    const t = setTimeout(() => setHighlightId(null), 6000);
    return () => clearTimeout(t);
  }, [highlightId]);

  useEffect(() => () => { if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current); }, []);

  // ── Mutations ─────────────────────────────────────────────────────────────
  const createMutation = useMutation({
    mutationFn: () => datasetsApi.create(newName, newDesc, newCategory, newProvenance),
    onSuccess: (ds) => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setShowCreate(false); setNewName(""); setNewDesc(""); setNewCategory("");
      setNewProvenance(EMPTY_PROVENANCE);
      // Make sure the new dataset is actually reachable before highlighting it: clear
      // any search, drop the rail to "All" only if its section isn't already selected,
      // and expand its section if collapsed.
      const targetKey = ds.category || UNCATEGORIZED;
      setSearch("");
      setSelectedCategory((prev) => (prev === null || prev === targetKey ? prev : null));
      setCollapsedCategories((prev) => {
        if (!prev.has(targetKey)) return prev;
        const next = new Set(prev);
        next.delete(targetKey);
        return next;
      });
      setHighlightId(ds.id);
      toast.success(`Dataset "${ds.name}" created`);
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Failed to create dataset")),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => datasetsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setDeleteTarget(null);
      toast.success("Dataset deleted");
    },
  });

  const renameMutation = useMutation({
    mutationFn: () =>
      datasetsApi.update(renameTarget!.id, {
        name: renameName,
        description: renameDesc,
        category: renameCategory,
        // Editing these retroactively changes every image that hasn't
        // overridden the field — the intended inheritance behaviour.
        ...renameProvenance,
      }),
    onSuccess: (ds) => {
      // Not just ["datasets"]: the provenance defaults edited here are the
      // effective source/license of every image that has not overridden them, so
      // gallery badges, Stats and any open Export preview are all stale now.
      invalidateProvenanceScope(qc);
      setRenameTarget(null);
      toast.success(`Updated "${ds.name}"`);
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Failed to update dataset")),
  });

  const rescanMutation = useMutation({
    mutationFn: (ds: Dataset) => datasetsApi.rescan(ds.id, true),
    onMutate: (ds: Dataset) => { setRescanTargetId(ds.id); },
    onSuccess: (data) => {
      toast.success("Rescanning folder…");
      setRescanJobId(data.job_id);
    },
    onError: () => { toast.error("Rescan failed"); setRescanTargetId(null); },
  });

  const captionImportMutation = useMutation({
    mutationFn: () => datasetsApi.importCaptions(captionImportTarget!.id, captionImportPath),
    onSuccess: (data) => {
      toast.success("Importing captions…");
      setCaptionImportTarget(null); setCaptionImportPath("");
      setCaptionJobId(data.job_id);
    },
    onError: () => toast.error("Caption import failed"),
  });

  const duplicateMutation = useMutation({
    mutationFn: () =>
      datasetsApi.duplicate(
        duplicateTarget!.id, duplicateName, duplicateVersionId,
        // Never send true alongside a snapshot: the backend 400s that pairing,
        // and the checkbox is disabled rather than cleared, so the state can
        // still read true while a version is picked.
        duplicateVersionId ? false : duplicateIncludeVideos,
      ),
    onSuccess: (data) => {
      setDuplicateTarget(null);
      setDuplicateJobId(data.job_id);
      toast.success("Duplicating dataset…");
    },
    onError: () => toast.error("Failed to duplicate dataset"),
  });

  /** Rename all datasets that belong to a category to a new name. */
  const renameCategoryMutation = useMutation({
    mutationFn: async ({ oldName, newName }: { oldName: string; newName: string }) => {
      const affected = datasets.filter((d) => d.category === oldName);
      await Promise.all(affected.map((d) => datasetsApi.update(d.id, { category: newName })));
    },
    onSuccess: (_data, { oldName, newName: renamedTo }) => {
      setEmptyCategories((prev) => prev.map((c) => (c === oldName ? renamedTo : c)));
      // Carry persisted UI state across the rename, or the user silently loses their
      // collapse state and rail selection lands on a name that no longer exists.
      setSelectedCategory((prev) => (prev === oldName ? renamedTo : prev));
      setCollapsedCategories((prev) => {
        if (!prev.has(oldName)) return prev;
        const next = new Set(prev);
        next.delete(oldName);
        next.add(renamedTo);
        return next;
      });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setRenamingCategory(null);
      toast.success("Category renamed");
    },
    onError: () => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      toast.error("Failed to rename category");
    },
  });

  /** Remove the category from all datasets that belong to it. */
  const deleteCategoryMutation = useMutation({
    mutationFn: async (catName: string) => {
      const affected = datasets.filter((d) => d.category === catName);
      await Promise.all(affected.map((d) => datasetsApi.update(d.id, { category: "" })));
    },
    onSuccess: (_data, catName) => {
      setEmptyCategories((prev) => prev.filter((c) => c !== catName));
      setSelectedCategory((prev) => (prev === catName ? null : prev));
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setDeletingCategory(null);
      toast.success("Category removed");
    },
    onError: () => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      toast.error("Failed to remove category");
    },
  });

  const moveCategoryMutation = useMutation({
    mutationFn: ({ datasetId, category }: { datasetId: string; category: string }) =>
      datasetsApi.update(datasetId, { category }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["datasets"] }),
    onError: () => toast.error("Failed to move dataset"),
  });

  // ── Drag/drop ─────────────────────────────────────────────────────────────
  const handleCardDrop = useCallback(async (datasetId: string, files: FileList) => {
    if (!files.length) return;
    try {
      // The dropped files are not filtered client-side: unlike the gallery grid
      // there is no competing per-card caption-drop gesture here, and sending
      // everything is what lets the server name what it declined. Reporting
      // files.length as images would call a rejected upload a successful one.
      const res = await imagesApi.upload(datasetId, Array.from(files));
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      qc.invalidateQueries({ queryKey: ["videos", datasetId] });
      showUploadSummaryToast(tallyUpload([res]));
    } catch {
      toast.error("Upload failed");
    }
  }, [qc]);

  const dragRafRef = useRef<number | null>(null);

  useEffect(() => {
    const el = pageRef.current;
    if (!el) return;

    const onDragOver = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      e.preventDefault();
      if (dragRafRef.current !== null) return;
      const cx = e.clientX;
      const cy = e.clientY;
      dragRafRef.current = requestAnimationFrame(() => {
        dragRafRef.current = null;
        const under = document.elementFromPoint(cx, cy);
        const card = under?.closest<HTMLElement>("[data-dataset-id]");
        const id = card?.dataset.datasetId ?? null;
        setDragOverId(id);
        if (dragTimerRef.current) clearTimeout(dragTimerRef.current);
        if (id) dragTimerRef.current = setTimeout(() => setDragOverId(null), 200);
      });
    };

    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      if (dragTimerRef.current) { clearTimeout(dragTimerRef.current); dragTimerRef.current = null; }
      setDragOverId(null);
      const under = document.elementFromPoint(e.clientX, e.clientY);
      const card = under?.closest<HTMLElement>("[data-dataset-id]");
      const id = card?.dataset.datasetId;
      if (id && e.dataTransfer?.files.length) handleCardDrop(id, e.dataTransfer.files);
    };

    el.addEventListener("dragover", onDragOver);
    el.addEventListener("drop", onDrop);
    return () => {
      el.removeEventListener("dragover", onDragOver);
      el.removeEventListener("drop", onDrop);
      if (dragRafRef.current !== null) cancelAnimationFrame(dragRafRef.current);
    };
  }, [handleCardDrop]);

  // ── New category handler ──────────────────────────────────────────────────
  function handleCreateCategory() {
    const name = newCategoryName.trim();
    if (!name) return;
    if (emptyCategories.includes(name) || datasets.some((d) => d.category === name)) {
      toast("Category already exists");
      return;
    }
    setEmptyCategories((prev) => [...prev, name]);
    setShowCreateCategory(false);
    setNewCategoryName("");
  }

  // ── Shared between the card and compact-row renderers ─────────────────────
  /** Identity, drag source, and click-through props every dataset element needs.
   *  `data-dataset-id` is also the file-upload drop target selector (see the native
   *  listeners on pageRef) — never put it on anything that isn't a dataset. */
  type DatasetElementProps = HTMLAttributes<HTMLDivElement> & {
    "data-dataset-id": string;
    draggable: boolean;
  };
  const datasetElementProps = (ds: Dataset): DatasetElementProps => ({
    "data-dataset-id": ds.id,
    draggable: hasAnyCategory,
    // Pane-aware navigation: must stay go(), not useNavigate — see docs/dev/panes-routing.md
    onClick: () => go(`/datasets/${ds.id}/gallery`, { page: "gallery", datasetId: ds.id }),
    onDragStart: (e) => {
      setDraggingDatasetId(ds.id);
      e.dataTransfer.setData("dataset-id", ds.id);
      e.dataTransfer.effectAllowed = "move";
    },
    onDragEnd: () => { setDraggingDatasetId(null); setDropTargetCategory(null); setRailDropTarget(null); },
  });

  /** The six per-dataset actions, shared by both densities. */
  const renderDatasetActions = (ds: Dataset, variant: "card" | "row") => {
    const btn = variant === "card"
      ? { width: 26, height: 26, background: "rgba(7,9,11,.7)", border: "1px solid var(--line-2)", backdropFilter: "blur(8px)" }
      : { width: 22, height: 22 };
    return (
      <div
        className="ds-row-actions"
        style={variant === "card"
          ? { position: "absolute", top: 10, right: 10, display: "flex", gap: 4, opacity: 0, transition: "opacity .15s", zIndex: 2 }
          : { display: "flex", gap: 2, opacity: 0, transition: "opacity .15s", flexShrink: 0, marginLeft: 4 }}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          className="icon-btn"
          title="Edit"
          style={btn}
          onClick={() => {
            setRenameTarget(ds);
            setRenameName(ds.name);
            setRenameDesc(ds.description ?? "");
            setRenameCategory(ds.category ?? "");
            setRenameProvenance({
              source_name: ds.source_name ?? "",
              source_url: ds.source_url ?? "",
              license: ds.license ?? "",
              attribution: ds.attribution ?? "",
            });
          }}
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M11.5 2.5l2 2-8 8H3.5v-2l8-8z"/>
          </svg>
        </button>
        <button
          className="icon-btn"
          title="Duplicate"
          style={btn}
          onClick={() => {
            setDuplicateTarget(ds);
            setDuplicateName(`${ds.name} (copy)`);
            setDupBranchId(undefined);
            setDuplicateVersionId(undefined);
            setDuplicateIncludeVideos(false);
          }}
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <rect x="5" y="5" width="8" height="9" rx="1"/><path d="M3 11V3a1 1 0 011-1h8"/>
          </svg>
        </button>
        <button
          className="icon-btn"
          title="Import folder"
          style={btn}
          onClick={() => { setImportInitialId(ds.id); setImportOpen(true); }}
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M2.5 3.5h4l1.5 2h5.5v7h-11v-9z"/>
          </svg>
        </button>
        <button
          className="icon-btn"
          title="Import captions (.txt sidecars from a folder)"
          style={btn}
          onClick={() => { setCaptionImportTarget(ds); setCaptionImportPath(""); }}
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M4 2.5h6l2.5 2.5v8.5h-8.5v-11z"/><path d="M5.5 8h5M5.5 10.5h3"/>
          </svg>
        </button>
        <button
          className="icon-btn"
          title="Rescan folder from disk"
          disabled={rescanTargetId === ds.id}
          style={btn}
          onClick={() => rescanMutation.mutate(ds)}
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2v3h-3"/>
          </svg>
        </button>
        <button
          className="icon-btn danger"
          title="Delete"
          style={btn}
          onClick={() => setDeleteTarget(ds)}
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M3 4.5h10M5.5 4.5V3a1 1 0 011-1h3a1 1 0 011 1v1.5M5.5 7.5v4M10.5 7.5v4M4 4.5l1 9h6l1-9"/>
          </svg>
        </button>
      </div>
    );
  };

  // ── Card renderer ─────────────────────────────────────────────────────────
  const renderCard = (ds: Dataset, i: number) => {
    const pct = ds.image_count ? Math.round((ds.captioned_count / ds.image_count) * 100) : 0;
    return (
      <div
        key={ds.id}
        {...datasetElementProps(ds)}
        // The card is a plain <div> with no role and no accessible name, and its
        // contents (counts, badges) repeat across every card, so an e2e
        // assertion about *this* dataset's card has nothing else to scope to.
        // One of the ≤3 testids frontend/e2e/helpers.ts budgets for.
        data-testid={`dataset-card-${ds.id}`}
        style={{
          background: "var(--surface-1)",
          border: `1px solid ${dragOverId === ds.id ? "var(--accent)" : "var(--line)"}`,
          borderRadius: "var(--r-lg)", overflow: "hidden",
          cursor: hasAnyCategory ? "grab" : "pointer", display: "flex", flexDirection: "column",
          position: "relative", transition: "border-color .15s, opacity .15s",
          opacity: draggingDatasetId === ds.id ? 0.45 : 1,
        }}
        onMouseEnter={(e) => { if (dragOverId !== ds.id) (e.currentTarget as HTMLElement).style.borderColor = "var(--line-2)"; }}
        onMouseLeave={(e) => { if (dragOverId !== ds.id) (e.currentTarget as HTMLElement).style.borderColor = "var(--line)"; }}
        className={"ds-card-wrapper" + (highlightId === ds.id ? " ds-flash" : "")}
      >
        {/* Drag-over overlay */}
        {dragOverId === ds.id && (
          <div style={{
            position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none",
            background: "rgba(0,0,0,0.55)", borderRadius: "var(--r-lg)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <div style={{
              background: "var(--surface-2)", border: "1px solid var(--accent)",
              borderRadius: "var(--r)", padding: "6px 14px",
              color: "var(--accent)", fontSize: 13, fontWeight: 600,
              display: "flex", alignItems: "center", gap: 6,
            }}>
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M8 10V2M5 5l3-3 3 3M2.5 13.5h11"/>
              </svg>
              Drop to upload
            </div>
          </div>
        )}

        {/* Preview tile strip */}
        <div style={{ height: 110, background: "var(--surface-2)", display: "grid", gridTemplateColumns: "repeat(8, 1fr)", gridTemplateRows: "1fr", gap: 1, position: "relative" }}>
          {(ds.preview_image_ids ?? []).length > 0
            ? Array.from({ length: 8 }).map((_, k) => {
                const imgId = ds.preview_image_ids[k % ds.preview_image_ids.length];
                return (
                  <div key={k} style={{ height: 110, overflow: "hidden", background: "var(--surface-3)" }}>
                    {/* draggable={false}: a default-draggable <img> inside a draggable
                        ancestor starts its own image drag carrying no "dataset-id",
                        silently breaking dataset→category drag from the preview strip. */}
                    <img
                      src={`/api/v1/images/${imgId}/thumbnail`}
                      alt=""
                      draggable={false}
                      style={{ width: "100%", height: 110, objectFit: "cover", display: "block" }}
                    />
                  </div>
                );
              })
            : Array.from({ length: 8 }).map((_, k) => (
                <div key={k} style={{ background: tileGrad(i, k) }} />
              ))
          }
          <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg, transparent 30%, var(--surface-1))", pointerEvents: "none" }} />
        </div>

        {renderDatasetActions(ds, "card")}

        {/* Body */}
        <div style={{ padding: "14px 16px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <h3 style={{ margin: "0 0 4px", fontSize: 14.5, fontWeight: 600, letterSpacing: "-.01em" }}>{ds.name}</h3>
            <p style={{ margin: 0, color: "var(--fg-mute)", fontSize: 12, lineHeight: 1.5, minHeight: 18 }}>
              {ds.description || <span style={{ color: "var(--fg-soft)" }}>No description</span>}
            </p>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
            {[
              { k: "Images", v: ds.image_count.toLocaleString(), accent: false },
              { k: "Captioned", v: `${pct}%`, accent: true },
              { k: "Size", v: formatSize(ds.total_size_bytes), accent: false },
            ].map(({ k, v, accent }) => (
              <div key={k} style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: "8px 10px" }}>
                <div style={{ color: "var(--fg-dim)", fontSize: 10.5, letterSpacing: ".04em", textTransform: "uppercase" }}>{k}</div>
                <div style={{ color: accent ? "var(--accent)" : "var(--fg)", fontSize: accent ? 16 : 14, fontWeight: 600, marginTop: 2, fontFeatureSettings: '"tnum"' }}>{v}</div>
              </div>
            ))}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--fg-dim)", fontSize: 11.5 }}>
            <span className="mono">{ds.captioned_count}/{ds.image_count} captioned</span>
            {/* Hidden at zero — a "0 videos" pill on every image-only dataset is
                pure noise. Videos are counted apart from image_count, so this is
                additional information, not a breakdown of the stat tiles above.
                Kept in step with the compact row renderer below. */}
            {ds.video_count > 0 && (
              <span className="badge" title={`${formatSize(ds.video_size_bytes)} of video`}>
                {ds.video_count} {ds.video_count === 1 ? "video" : "videos"}
              </span>
            )}
            {ds.source_name && (
              <span style={{
                minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                color: "var(--fg-soft)",
              }} title={ds.source_name}>{ds.source_name}</span>
            )}
            <span style={{ marginLeft: "auto", display: "flex" }}><LicenseBadge ds={ds} /></span>
          </div>
        </div>
      </div>
    );
  };

  const renderGrid = (items: Dataset[]) => (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 14 }}>
      {items.map((ds, i) => renderCard(ds, i))}
    </div>
  );

  // ── Compact row renderer ──────────────────────────────────────────────────
  // ~44px per dataset instead of ~275px. Shares datasetElementProps with the card so
  // dataset→category drag, file-drop upload, and pane navigation behave identically.
  const renderRow = (ds: Dataset, i: number) => {
    const pct = ds.image_count ? Math.round((ds.captioned_count / ds.image_count) * 100) : 0;
    const thumb = ds.preview_image_ids?.[0];
    return (
      <div
        key={ds.id}
        {...datasetElementProps(ds)}
        className={"ds-list-row" + (highlightId === ds.id ? " ds-flash" : "")}
        style={{
          display: "flex", alignItems: "center", gap: 10, height: 44, padding: "0 8px 0 6px",
          background: "var(--surface-1)",
          border: `1px solid ${dragOverId === ds.id ? "var(--accent)" : "var(--line)"}`,
          borderRadius: "var(--r)", position: "relative", overflow: "hidden",
          cursor: hasAnyCategory ? "grab" : "pointer",
          opacity: draggingDatasetId === ds.id ? 0.45 : 1,
          transition: "border-color .15s, opacity .15s",
        }}
      >
        {dragOverId === ds.id && (
          <div style={{
            position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none",
            background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--accent)", fontSize: 12, fontWeight: 600,
          }}>
            Drop to upload
          </div>
        )}

        <div style={{
          width: 36, height: 36, flexShrink: 0, borderRadius: "var(--r-sm)", overflow: "hidden",
          background: thumb ? "var(--surface-3)" : tileGrad(i, 0),
        }}>
          {thumb && (
            <img
              src={`/api/v1/images/${thumb}/thumbnail`}
              alt=""
              draggable={false}
              style={{ width: 36, height: 36, objectFit: "cover", display: "block" }}
            />
          )}
        </div>

        <span style={{
          fontSize: 13, fontWeight: 600, flexShrink: 0, maxWidth: 240,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{ds.name}</span>

        <span style={{
          fontSize: 12, color: "var(--fg-mute)", flex: 1, minWidth: 0,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{ds.description}</span>

        <LicenseBadge ds={ds} />

        <span className="mono" style={{
          display: "flex", gap: 14, flexShrink: 0, fontSize: 11.5,
          color: "var(--fg-dim)", fontFeatureSettings: '"tnum"',
        }}>
          <span>{ds.image_count.toLocaleString()} img</span>
          {/* Same hidden-at-zero rule as the card footer; both renderers change
              together or the two views disagree about the same dataset. */}
          {ds.video_count > 0 && <span>{ds.video_count.toLocaleString()} vid</span>}
          <span style={{ color: "var(--accent)" }}>{pct}%</span>
          <span>{formatSize(ds.total_size_bytes)}</span>
        </span>

        {renderDatasetActions(ds, "row")}
      </div>
    );
  };

  const renderRows = (items: Dataset[]) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {items.map((ds, i) => renderRow(ds, i))}
    </div>
  );

  const renderItems = (items: Dataset[]) =>
    density === "rows" ? renderRows(items) : renderGrid(items);

  const toggleCategory = (cat: string) => {
    setCollapsedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat); else next.add(cat);
      return next;
    });
  };

  // `forceExpanded` is set when the rail has filtered to a single category: a
  // persisted-collapsed section would otherwise render as a header over nothing.
  const renderCategorySection = (
    cat: string, items: Dataset[], muted = false, opts?: { forceExpanded?: boolean },
  ) => {
    const collapsed = !opts?.forceExpanded && collapsedCategories.has(cat);
    const isUncategorized = cat === UNCATEGORIZED;
    const isRenaming = renamingCategory === cat;
    const isDropTarget = dropTargetCategory === cat;

    return (
      <div
        key={cat}
        style={{ marginBottom: 22, borderRadius: 8, outline: isDropTarget ? "2px solid var(--accent)" : "2px solid transparent", transition: "outline-color .1s" }}
        onDragOver={(e) => {
          if (!e.dataTransfer.types.includes("dataset-id")) return;
          e.preventDefault();
          if (dropTargetCategory !== cat) setDropTargetCategory(cat);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropTargetCategory(null);
        }}
        onDrop={(e) => {
          e.preventDefault();
          const datasetId = e.dataTransfer.getData("dataset-id");
          if (!datasetId) return;
          const targetCat = isUncategorized ? "" : cat;
          const src = datasets.find((d) => d.id === datasetId);
          if (src && src.category !== targetCat) moveCategoryMutation.mutate({ datasetId, category: targetCat });
          setDropTargetCategory(null);
          setDraggingDatasetId(null);
        }}
      >
        {/* Section header */}
        <div
          className="ds-cat-header"
          style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}
        >
          {/* Left: folder icon + name/input + badge + chevron — all clickable for toggle */}
          <div
            style={{ display: "flex", alignItems: "center", gap: 8, cursor: isRenaming ? "default" : "pointer", userSelect: "none", flex: 1, minWidth: 0 }}
            onClick={() => { if (!isRenaming) toggleCategory(cat); }}
          >
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke={muted ? "var(--fg-mute)" : "var(--fg-dim)"} strokeWidth="1.4" style={{ flexShrink: 0 }}>
              <path d="M2.5 3.5h4l1.5 2h5.5v7h-11v-9z"/>
            </svg>

            {isRenaming ? (
              <input
                className="input"
                value={renameCategoryValue}
                onChange={(e) => setRenameCategoryValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && renameCategoryValue.trim())
                    renameCategoryMutation.mutate({ oldName: cat, newName: renameCategoryValue.trim() });
                  if (e.key === "Escape") setRenamingCategory(null);
                }}
                // eslint-disable-next-line jsx-a11y/no-autofocus
                autoFocus
                style={{ fontSize: 13, fontWeight: 600, width: 200, padding: "2px 8px", height: 28 }}
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <span style={{ fontWeight: 600, fontSize: 13, color: muted ? "var(--fg-mute)" : "var(--fg)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {cat}
              </span>
            )}

            <span className="badge" style={{ flexShrink: 0 }}>{items.length}</span>

            {!isRenaming && !opts?.forceExpanded && (
              <svg
                width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="var(--fg-mute)" strokeWidth="1.4"
                style={{ flexShrink: 0, transform: collapsed ? "rotate(-90deg)" : "none", transition: "transform .15s" }}
              >
                <path d="M2 4l4 4 4-4"/>
              </svg>
            )}
          </div>

          {/* Right: management buttons (not for Uncategorized) */}
          {!isUncategorized && (
            <div
              className="ds-cat-actions"
              style={{ display: "flex", gap: 4, flexShrink: 0, opacity: isRenaming ? 1 : 0, transition: "opacity .15s" }}
              onClick={(e) => e.stopPropagation()}
            >
              {isRenaming ? (
                <>
                  {/* Confirm rename */}
                  <button
                    className="icon-btn"
                    title="Save"
                    disabled={!renameCategoryValue.trim() || renameCategoryMutation.isPending}
                    style={{ width: 26, height: 26 }}
                    onClick={() => renameCategoryMutation.mutate({ oldName: cat, newName: renameCategoryValue.trim() })}
                  >
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M2.5 8.5l4 4 7-8"/>
                    </svg>
                  </button>
                  {/* Cancel rename */}
                  <button
                    className="icon-btn"
                    title="Cancel"
                    style={{ width: 26, height: 26 }}
                    onClick={() => setRenamingCategory(null)}
                  >
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M3 3l10 10M13 3L3 13"/>
                    </svg>
                  </button>
                </>
              ) : (
                <>
                  {/* Rename category */}
                  <button
                    className="icon-btn"
                    title="Rename category"
                    style={{ width: 26, height: 26 }}
                    onClick={() => { setRenamingCategory(cat); setRenameCategoryValue(cat); }}
                  >
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
                      <path d="M11.5 2.5l2 2-8 8H3.5v-2l8-8z"/>
                    </svg>
                  </button>
                  {/* Delete category */}
                  <button
                    className="icon-btn danger"
                    title="Delete category"
                    style={{ width: 26, height: 26 }}
                    onClick={() => setDeletingCategory(cat)}
                  >
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
                      <path d="M3 4.5h10M5.5 4.5V3a1 1 0 011-1h3a1 1 0 011 1v1.5M5.5 7.5v4M10.5 7.5v4M4 4.5l1 9h6l1-9"/>
                    </svg>
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        {!collapsed && (items.length > 0 ? renderItems(items) : (
          <div style={{
            height: 68, border: `2px dashed ${isDropTarget ? "var(--accent)" : "var(--line)"}`,
            borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center",
            color: isDropTarget ? "var(--accent)" : "var(--fg-mute)", fontSize: 13,
            transition: "border-color .1s, color .1s",
          }}>
            {isDropTarget ? "Release to move here" : "Empty — drag datasets here"}
          </div>
        ))}
      </div>
    );
  };

  // ── Category rail ─────────────────────────────────────────────────────────
  // A filter, not a replacement for sections: picking a category still renders through
  // renderCategorySection, so collapse/rename/delete/drop all stay in one place.
  // `key === null` is the "All" pseudo-entry.
  const renderRailRow = (key: string | null, label: string, count: number) => {
    const isActive = effectiveSelected === key;
    const isDrop = key !== null && railDropTarget === key;
    // "All" is not a drop target — there is no category to assign.
    // NB: never add data-dataset-id here; it is the file-upload target selector and
    // would make handleCardDrop POST images to a category name as if it were an id.
    const dropHandlers = key === null ? {} : {
      onDragOver: (e: ReactDragEvent) => {
        if (!e.dataTransfer.types.includes("dataset-id")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        if (railDropTarget !== key) setRailDropTarget(key);
      },
      onDragLeave: (e: ReactDragEvent) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setRailDropTarget(null);
      },
      onDrop: (e: ReactDragEvent) => {
        e.preventDefault();
        const datasetId = e.dataTransfer.getData("dataset-id");
        setRailDropTarget(null);
        setDraggingDatasetId(null);
        if (!datasetId) return;
        const target = key === UNCATEGORIZED ? "" : key;
        const src = datasets.find((d) => d.id === datasetId);
        if (src && src.category !== target) moveCategoryMutation.mutate({ datasetId, category: target });
      },
    };

    return (
      <button
        key={key ?? "__all__"}
        className="ds-rail-row"
        title={label}
        onClick={() => setSelectedCategory(key)}
        {...dropHandlers}
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6,
          width: "100%", padding: "5px 8px", borderRadius: "var(--r)",
          textAlign: "left", cursor: "pointer",
          // transparent border rather than none, so the drop highlight doesn't shift the row
          border: `1px solid ${isDrop ? "var(--accent)" : "transparent"}`,
          background: isDrop ? "var(--accent-glow)" : isActive ? "var(--surface-3)" : "transparent",
          color: isActive ? "var(--accent)" : "var(--fg)",
          fontSize: 12.5, fontWeight: isActive ? 600 : 400,
          fontStyle: key === UNCATEGORIZED ? "italic" : undefined,
        }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <span className="ds-rail-count" style={{ fontSize: 11, color: "var(--fg-mute)", flexShrink: 0 }}>{count}</span>
      </button>
    );
  };

  const renderRail = () => (
    <aside style={{
      width: 180, flexShrink: 0, borderRight: "1px solid var(--line)",
      overflowY: "auto", padding: "10px 6px", background: "var(--surface-1)",
      // Dimmed while searching: rail selection is bypassed so no match can hide
      // behind an unselected category.
      opacity: isSearching ? 0.55 : 1, transition: "opacity .12s",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "2px 8px 6px" }}>
        <span style={{
          fontSize: 10, fontWeight: 600, letterSpacing: ".08em",
          color: "var(--fg-mute)", textTransform: "uppercase",
        }}>
          Categories
        </span>
        <button
          className="icon-btn"
          style={{ width: 20, height: 20 }}
          title="New category"
          onClick={() => setShowCreateCategory(true)}
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M8 3v10M3 8h10"/>
          </svg>
        </button>
      </div>
      {renderRailRow(null, "All", filteredAndSorted.length)}
      {sectionKeys.map((k) => renderRailRow(k, k, sectionItems.get(k)?.length ?? 0))}
    </aside>
  );

  // ── Grouped layout ────────────────────────────────────────────────────────
  const { hasAnyCategory, categoryNames, uncategorized } = useMemo(() => {
    const activeEmpties = search ? [] : emptyCategories;
    const anyCategory = filteredAndSorted.some((d) => d.category) || activeEmpties.length > 0;
    return {
      hasAnyCategory: anyCategory,
      categoryNames: anyCategory
        ? ([...new Set([
            ...filteredAndSorted.map((d) => d.category).filter(Boolean),
            ...activeEmpties,
          ])].sort() as string[])
        : [],
      uncategorized: anyCategory ? filteredAndSorted.filter((d) => !d.category) : [],
    };
  }, [filteredAndSorted, emptyCategories, search]);

  // Datasets bucketed by section key, in one pass — the previous per-section .filter()
  // was O(categories × datasets) on every render.
  // Deliberately not memoized: this is a single pass over the dataset list, which is
  // far cheaper than the bookkeeping a useMemo would add.
  const sectionItems = (() => {
    const m = new Map<string, Dataset[]>();
    for (const k of categoryNames) m.set(k, []);
    for (const d of filteredAndSorted) {
      if (!d.category) continue;
      m.get(d.category)?.push(d);
    }
    if (uncategorized.length > 0) m.set(UNCATEGORIZED, uncategorized);
    return m;
  })();

  // Uncategorized first — a newly created dataset has no category and must not be
  // pushed below every named section.
  const sectionKeys = [...(uncategorized.length > 0 ? [UNCATEGORIZED] : []), ...categoryNames];

  // A persisted selection that no longer exists is handled by derivation, never by
  // clearing it in an effect — on first paint `datasets` is [], so eager cleanup would
  // wipe a still-valid selection while the list loads.
  const isSearching = search.trim() !== "";
  const effectiveSelected =
    !isSearching && selectedCategory && sectionKeys.includes(selectedCategory) ? selectedCategory : null;
  const visibleSectionKeys = effectiveSelected ? [effectiveSelected] : sectionKeys;
  const showRail = hasAnyCategory && sectionKeys.length >= 2;
  const allCollapsed = sectionKeys.length > 0 && sectionKeys.every((k) => collapsedCategories.has(k));

  // Free-text licenses already recorded in the dataset being edited — offered as
  // options for its default. Idle (never fetched) while no edit modal is open.
  const renameCustomLicenses = useCustomLicenses(renameTarget?.id);

  // ── Rename modal: changed detection ───────────────────────────────────────
  const renameChanged = renameTarget
    ? renameName !== renameTarget.name ||
      renameDesc !== (renameTarget.description ?? "") ||
      renameCategory !== (renameTarget.category ?? "") ||
      renameProvenance.source_name !== (renameTarget.source_name ?? "") ||
      renameProvenance.source_url !== (renameTarget.source_url ?? "") ||
      renameProvenance.license !== (renameTarget.license ?? "") ||
      renameProvenance.attribution !== (renameTarget.attribution ?? "")
    : false;

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div ref={pageRef} style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Page header */}
      <div className="page-h" style={{ padding: "24px 28px 0", marginBottom: 16, flexShrink: 0 }}>
        <div>
          <h1>Datasets</h1>
          <p>
            {datasets.length} datasets · {totalImages.toLocaleString()} images · {formatSize(totalSize)} on disk
          </p>
        </div>
        {/* Wraps rather than overflowing: this toolbar is wide and the page also
            renders inside narrow split-view panes. */}
        <div className="phactions" style={{ flexWrap: "wrap", justifyContent: "flex-end" }}>
          <div className="search-wrap">
            <svg className="search-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/>
            </svg>
            <input
              className="input"
              placeholder="Search datasets…"
              style={{ width: 220 }}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            className="select"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            style={{ width: 170 }}
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>

          {/* Density toggle — cards vs ~44px compact rows */}
          <div style={{
            display: "flex", gap: 2, padding: 2, border: "1px solid var(--line-2)",
            borderRadius: "var(--r)", background: "var(--surface-2)",
          }}>
            {(["grid", "rows"] as const).map((m) => (
              <button
                key={m}
                className="icon-btn"
                aria-pressed={density === m}
                title={m === "grid" ? "Card grid" : "Compact rows"}
                onClick={() => setDensity(m)}
                style={{
                  width: 26, height: 24,
                  background: density === m ? "var(--surface-3)" : "transparent",
                  color: density === m ? "var(--accent)" : "var(--fg-mute)",
                }}
              >
                {m === "grid" ? (
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
                    <rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="5" rx="1"/>
                    <rect x="2" y="9" width="5" height="5" rx="1"/><rect x="9" y="9" width="5" height="5" rx="1"/>
                  </svg>
                ) : (
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
                    <path d="M2 4h12M2 8h12M2 12h12"/>
                  </svg>
                )}
              </button>
            ))}
          </div>

          {hasAnyCategory && !effectiveSelected && (
            <button
              className="btn"
              title={allCollapsed ? "Expand all categories" : "Collapse all categories"}
              onClick={() => setCollapsedCategories(allCollapsed ? new Set() : new Set(sectionKeys))}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                {allCollapsed ? <path d="M4 6l4 4 4-4"/> : <path d="M4 10l4-4 4 4"/>}
              </svg>
              {allCollapsed ? "Expand all" : "Collapse all"}
            </button>
          )}

          <button className="btn" onClick={() => { setImportInitialId(undefined); setImportOpen(true); }}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M2.5 3.5h4l1.5 2h5.5v7h-11v-9z"/>
            </svg>
            Import folder
          </button>
          <button className="btn" onClick={() => setShowCreateCategory(true)}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M2 4h12M2 8h8M2 12h5"/><path d="M13 10v4M11 12h4"/>
            </svg>
            New category
          </button>
          <button className="btn primary" onClick={() => setShowCreate(true)}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M8 3v10M3 8h10"/>
            </svg>
            New dataset
          </button>
        </div>
      </div>

      {/* Rail + content. pageRef stays on the root so the native file-drop listeners
          still cover both columns. */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {showRail && renderRail()}

        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "0 28px 28px" }}>
          {isLoading && <p style={{ color: "var(--fg-mute)" }}>Loading…</p>}

          {/* Sections (Uncategorized first, via sectionKeys) or a flat grid */}
          {hasAnyCategory
            ? visibleSectionKeys.map((k) =>
                renderCategorySection(
                  k,
                  sectionItems.get(k) ?? [],
                  k === UNCATEGORIZED,
                  { forceExpanded: !!effectiveSelected },
                ))
            : renderItems(filteredAndSorted)}
        </div>
      </div>

      {/* Hover reveals for card/row actions, category actions, and rail rows.
          Kept local (not index.css) alongside the inline-style convention on this page. */}
      <style>{`
        .ds-card-wrapper:hover .ds-row-actions { opacity: 1 !important; }
        .ds-list-row:hover .ds-row-actions { opacity: 1 !important; }
        .ds-list-row:hover { border-color: var(--line-2) !important; }
        .ds-cat-header:hover .ds-cat-actions { opacity: 1 !important; }
        .ds-rail-row:hover { background: var(--surface-2); }
        .ds-rail-row:hover .ds-rail-count { color: var(--fg); }
      `}</style>

      {/* ── Edit Dataset Modal ─────────────────────────────────────────────── */}
      {renameTarget && (
        <div className="dialog-bg">
          <div className="dialog">
            <h3>Edit Dataset</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 18 }}>
              <div>
                <label className="label">Name</label>
                <input
                  className="input"
                  value={renameName}
                  autoFocus
                  onChange={(e) => setRenameName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && renameName && renameChanged) renameMutation.mutate();
                  }}
                />
              </div>
              <div>
                <label className="label">Description <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}>(optional)</span></label>
                <input
                  className="input"
                  placeholder="…"
                  value={renameDesc}
                  onChange={(e) => setRenameDesc(e.target.value)}
                />
              </div>
              <CategoryPicker
                value={renameCategory}
                onChange={setRenameCategory}
                existingCategories={existingCategories}
                label="Category"
                labelNote="(optional — groups datasets into folders)"
                autoFocusNew={false}
              />
              <ProvenanceFields
                value={renameProvenance}
                onChange={setRenameProvenance}
                note="Defaults for every image in this dataset that hasn't set its own. Changing them updates all non-overridden images."
                customLicenses={renameCustomLicenses}
              />
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn ghost" onClick={() => setRenameTarget(null)}>Cancel</button>
              <button
                className="btn primary"
                onClick={() => renameMutation.mutate()}
                disabled={!renameName || !renameChanged || renameMutation.isPending}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Create Modal ───────────────────────────────────────────────────── */}
      {showCreate && (
        <div className="dialog-bg">
          <div className="dialog">
            <h3>New Dataset</h3>
            <p>Give it a name and optional description.</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 18 }}>
              <div>
                <label className="label">Name</label>
                <input className="input" placeholder="my_dataset" value={newName} autoFocus
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && newName && createMutation.mutate()} />
              </div>
              <div>
                <label className="label">Description <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}>(optional)</span></label>
                <input className="input" placeholder="…" value={newDesc} onChange={(e) => setNewDesc(e.target.value)} />
              </div>
              <CategoryPicker
                value={newCategory}
                onChange={setNewCategory}
                existingCategories={existingCategories}
                label="Category"
                labelNote="(optional)"
                autoFocusNew={false}
              />
              <ProvenanceFields
                value={newProvenance}
                onChange={setNewProvenance}
                note="Defaults inherited by every image in this dataset unless the image sets its own."
              />
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn ghost" onClick={() => { setShowCreate(false); setNewName(""); setNewDesc(""); setNewCategory(""); setNewProvenance(EMPTY_PROVENANCE); }}>Cancel</button>
              <button className="btn primary" onClick={() => createMutation.mutate()} disabled={!newName || createMutation.isPending}>Create</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Import Modal ───────────────────────────────────────────────────── */}
      {importOpen && (
        <ImportFolderModal
          datasets={datasets}
          initialDatasetId={importInitialId}
          onStarted={setImportJobId}
          onClose={() => setImportOpen(false)}
        />
      )}

      {/* ── Import Captions Modal ──────────────────────────────────────────── */}
      {captionImportTarget && (
        <div className="dialog-bg">
          <div className="dialog">
            <h3>Import captions from folder</h3>
            <p>Into: <strong style={{ color: "var(--fg)" }}>{captionImportTarget.name}</strong></p>
            <p style={{ fontSize: 12, color: "var(--fg-mute)", marginTop: -4, marginBottom: 14 }}>
              Matches each <code>.txt</code> file to an image by filename and overwrites its caption.
            </p>
            <div style={{ marginBottom: 18 }}>
              <label className="label">Folder path</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input className="input" placeholder="/home/user/captions or D:\captions" value={captionImportPath}
                  onChange={(e) => setCaptionImportPath(e.target.value)} autoFocus style={{ flex: 1 }}
                  onKeyDown={(e) => { if (e.key === "Enter" && captionImportPath && !captionImportMutation.isPending) captionImportMutation.mutate(); }} />
                <button className="btn" onClick={() => setCaptionDirPickerOpen(true)}>Browse…</button>
              </div>
            </div>
            {captionDirPickerOpen && (
              <DirPickerModal
                initialPath={captionImportPath}
                title="Select a caption folder"
                confirmLabel="Use folder"
                onConfirm={(p) => { setCaptionImportPath(p); setCaptionDirPickerOpen(false); }}
                onCancel={() => setCaptionDirPickerOpen(false)}
              />
            )}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn ghost" onClick={() => { setCaptionImportTarget(null); setCaptionImportPath(""); }}>Cancel</button>
              <button className="btn primary" onClick={() => captionImportMutation.mutate()} disabled={!captionImportPath || captionImportMutation.isPending}>Import captions</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Duplicate Modal ────────────────────────────────────────────────── */}
      {duplicateTarget && (
        <div className="dialog-bg">
          <div className="dialog">
            <h3>Duplicate Dataset</h3>
            <p style={{ marginBottom: 16 }}>
              Source: <strong style={{ color: "var(--fg)" }}>{duplicateTarget.name}</strong>
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 18 }}>
              <div>
                <label className="label">New name</label>
                <input
                  className="input"
                  autoFocus
                  value={duplicateName}
                  onChange={(e) => setDuplicateName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && duplicateName && !duplicateMutation.isPending)
                      duplicateMutation.mutate();
                  }}
                />
              </div>

              {/* Snapshot source — only shown when versioning is enabled */}
              {versioningEnabled && (
                <div>
                  <label className="label" style={{ marginBottom: 8 }}>Source snapshot</label>
                  {dupBranches.length === 0 ? (
                    <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
                      No snapshots yet — will duplicate current state
                    </p>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span style={{ fontSize: 12, color: "var(--fg-mute)", minWidth: 50 }}>Branch</span>
                        <select
                          className="select"
                          style={{ flex: 1 }}
                          value={resolvedDupBranchId ?? ""}
                          onChange={(e) => {
                            setDupBranchId(e.target.value || undefined);
                            setDuplicateVersionId(undefined);
                          }}
                        >
                          {dupBranches.map((b) => (
                            <option key={b.id} value={b.id}>{b.name}</option>
                          ))}
                        </select>
                      </div>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span style={{ fontSize: 12, color: "var(--fg-mute)", minWidth: 50 }}>Version</span>
                        <select
                          className="select"
                          style={{ flex: 1 }}
                          value={duplicateVersionId ?? ""}
                          onChange={(e) => setDuplicateVersionId(e.target.value || undefined)}
                        >
                          <option value="">Current state</option>
                          {dupVersions.map((v) => (
                            <option key={v.id} value={v.id}>
                              {v.name ?? "Snapshot"} — {formatDate(v.created_at)}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Videos are counted apart from images and are not copied by
                  default — so the cost is spelled out from the columns the card
                  already shows, and the row only exists when there is footage
                  to carry. */}
              {duplicateTarget.video_count > 0 && (
                <div>
                  <label
                    className="label"
                    style={{
                      display: "flex", alignItems: "center", gap: 8, marginBottom: 0,
                      cursor: duplicateVersionId ? "not-allowed" : "pointer",
                      opacity: duplicateVersionId ? 0.5 : 1,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={!duplicateVersionId && duplicateIncludeVideos}
                      disabled={!!duplicateVersionId}
                      onChange={(e) => setDuplicateIncludeVideos(e.target.checked)}
                    />
                    Copy {duplicateTarget.video_count}{" "}
                    {duplicateTarget.video_count === 1 ? "video" : "videos"}{" "}
                    ({formatSize(duplicateTarget.video_size_bytes)})
                  </label>
                  {duplicateVersionId && (
                    <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "6px 0 0 24px" }}>
                      Snapshots don't capture videos
                    </p>
                  )}
                </div>
              )}
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                className="btn ghost"
                onClick={() => { setDuplicateTarget(null); }}
              >
                Cancel
              </button>
              <button
                className="btn primary"
                onClick={() => duplicateMutation.mutate()}
                disabled={!duplicateName || duplicateMutation.isPending}
              >
                Duplicate
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete dataset confirm ─────────────────────────────────────────── */}
      {deleteTarget && (
        <ConfirmDialog
          title="Delete dataset"
          message={`Delete "${deleteTarget.name}" and all its images? This cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {/* ── Delete category confirm ────────────────────────────────────────── */}
      {deletingCategory && (
        <ConfirmDialog
          title="Remove category"
          message={`Remove the "${deletingCategory}" category from all ${datasets.filter((d) => d.category === deletingCategory).length} dataset(s)? The datasets themselves won't be deleted.`}
          confirmLabel="Remove"
          danger
          onConfirm={() => deleteCategoryMutation.mutate(deletingCategory)}
          onCancel={() => setDeletingCategory(null)}
        />
      )}

      {/* ── New Category modal ─────────────────────────────────────────────── */}
      {showCreateCategory && (
        <div
          className="dialog-bg"
          onClick={() => { setShowCreateCategory(false); setNewCategoryName(""); }}
        >
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>New Category</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 18 }}>
              <label className="label">Category name</label>
              <input
                className="input"
                autoFocus
                placeholder="e.g. Portraits"
                value={newCategoryName}
                onChange={(e) => setNewCategoryName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreateCategory();
                  if (e.key === "Escape") { setShowCreateCategory(false); setNewCategoryName(""); }
                }}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="btn" onClick={() => { setShowCreateCategory(false); setNewCategoryName(""); }}>
                Cancel
              </button>
              <button
                className="btn primary"
                disabled={!newCategoryName.trim()}
                onClick={handleCreateCategory}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
