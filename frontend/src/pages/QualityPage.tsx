import { useState, useEffect, useMemo, useRef } from "react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../utils/apiError";
import { qualityApi, type DuplicateGroup, type DuplicateImage } from "../api/quality";
import ConfirmDialog from "../components/common/ConfirmDialog";
import { formatFramePosition } from "../utils/duration";
import { settingsApi, type Thresholds } from "../api/settings";
import { datasetsApi } from "../api/datasets";
import { imagesApi } from "../api/images";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import StyleReferencePicker from "../components/quality/StyleReferencePicker";
import { DINO_LAYER_LABELS } from "../constants/dinoLabels";
import { QUALITY_WORKFLOW_KEY, QUALITY_FILTERS_PREFIX } from "../constants/storage";
import { loadPersisted, clearPersisted, datasetScopedKey } from "../utils/persistentState";
import { useDebouncedPersist } from "../hooks/useDebouncedPersist";

interface QualityWorkflow {
  runAesthetic: boolean;
  runTechnical: boolean;
  runWatermark: boolean;
  runEmbeddings: boolean;
  runDino: boolean;
  runNsfw: boolean;
  runDinoLayers: boolean;
  embeddingType: "clip" | "dino" | "combined";
  dinoLayer: number | "all" | null;
}

/** Rank a duplicate group best-first for *Keep best*, nulls **last**.
 *
 *  The naive `(b.aesthetic_score ?? 0) - (a.aesthetic_score ?? 0)` sorts an
 *  unscored image below a 0.1 and so deletes it preferentially — exactly
 *  backwards, since an unscored image is unknown, not bad. Scored images rank by
 *  score descending; unscored ones keep their incoming (created_at) order behind
 *  all of them. The caller disables the button entirely when nothing is scored.
 */
function rankForKeepBest(group: DuplicateGroup): DuplicateImage[] {
  return [...group].sort((a, b) => {
    const as = a.aesthetic_score, bs = b.aesthetic_score;
    if (as == null && bs == null) return 0;
    if (as == null) return 1;
    if (bs == null) return -1;
    return bs - as;
  });
}

/** The one video every member of a group came from, or null.
 *
 *  Non-null only when *every* member carries the same non-null
 *  `source_video_id` — a mixed group gets per-thumbnail labels instead, because
 *  a banner saying "these all came from clip.mp4" would be false there.
 */
function sharedSourceVideo(group: DuplicateGroup): { id: string; name: string | null } | null {
  const first = group[0]?.source_video_id;
  if (!first) return null;
  if (!group.every((m) => m.source_video_id === first)) return null;
  return { id: first, name: group[0].source_video_name };
}

/** Everything a group card and the bulk bar need, derived once per payload.
 *
 *  `rootId` is `group[0].id` — the scan's kept image, unique per group and stable
 *  across refetches, so it serves as the React key *and* the expand key. The
 *  array index would be neither.
 */
interface DupGroupMeta {
  group: DuplicateGroup;
  rootId: string;
  shared: { id: string; name: string | null } | null;
  anyScored: boolean;
  anyLineage: boolean;
}

/** Which groups the panel shows, and what the bulk buttons therefore cover.
 *
 *  A clean partition rather than overlapping predicates: every group is in
 *  exactly one of `video` / `other`, so the two chip counts always sum to the
 *  `all` count and a bulk run over each in turn touches every group once.
 */
type DupFilter = "all" | "video" | "other";

/** A duplicate resolution held for the confirm dialog — one group or a bulk run.
 *
 *  Both variants carry `plans`: one ordered member array per group, where
 *  `plans[i][0]` survives and the rest are deleted. That is exactly what the
 *  mutation takes, so the two paths differ only in what the dialog says.
 */
type PendingResolve =
  | {
      kind: "group";
      mode: "best" | "first";
      plans: DuplicateImage[][];
      videoName: string | null;
      timestamps: (number | null)[];
    }
  | {
      kind: "bulk";
      mode: "best" | "first";
      plans: DuplicateImage[][];
      sameSource: number;
      skipped: number;
    };

/** Total images a plan deletes — every member of every group but the survivor. */
function plannedDeletions(plans: DuplicateImage[][]): number {
  return plans.reduce((n, g) => n + g.length - 1, 0);
}

/** The confirm copy for a resolution — written once for all four buttons.
 *
 *  The hazard the dialog exists for — a held animation cel or recycled footage
 *  hashing identical to a redundant copy — is a property of the *group*, not of
 *  the ranking heuristic, so *Keep first* deletes exactly the same N−1 frames on
 *  one click and earns the same beat of friction. Only the survivor clause
 *  differs; a second hardcoded message block would drift the caveat, and that
 *  argument holds harder over 138 groups than over one.
 */
function resolveConfirmCopy(p: PendingResolve): { title: string; message: string } {
  const survivor = p.mode === "best"
    ? "keeps the highest-scoring one"
    : "keeps the one the duplicate scan picked, which is not necessarily the best";
  if (p.kind === "group") {
    const n = plannedDeletions(p.plans);
    return {
      title: "Delete frames from one video?",
      message:
        `Every image in this group was extracted from ${p.videoName ?? "the same video"}. ` +
        `This deletes ${n} frame${n === 1 ? "" : "s"} at ` +
        `${p.timestamps.map((t) => formatFramePosition(t)).join(", ")} and ${survivor}. ` +
        `Held animation cels and recycled footage hash the same as a redundant copy, so these may be distinct shots.`,
    };
  }
  // Bulk always confirms, even with no same-source group among them: it is a
  // many-image irreversible delete over groups the user has not read.
  const groups = p.plans.length;
  const n = plannedDeletions(p.plans);
  let message =
    `This deletes ${n} image${n === 1 ? "" : "s"} across ` +
    `${groups} group${groups === 1 ? "" : "s"} and ${survivor} in each. This cannot be undone.`;
  if (p.sameSource > 0) {
    message +=
      ` ${p.sameSource} of them ${p.sameSource === 1 ? "is" : "are"} made up entirely of frames from ` +
      `one video — held animation cels and recycled footage hash the same as a redundant copy, ` +
      `so those may be distinct shots.`;
  }
  if (p.skipped > 0) {
    message +=
      ` ${p.skipped} group${p.skipped === 1 ? "" : "s"} ` +
      `${p.skipped === 1 ? "is" : "are"} skipped: nothing in ` +
      `${p.skipped === 1 ? "it" : "them"} has an aesthetic score, so there is no best to keep.`;
  }
  return {
    title: `Resolve ${groups} duplicate group${groups === 1 ? "" : "s"}?`,
    message,
  };
}

/** Groups per `resolveDuplicates` call in a bulk run.
 *
 *  Each group is independent, so partial application is coherent — this is not a
 *  transaction that must land whole. Batching keeps the request bounded (the
 *  endpoint's versioning hook copies bytes per deleted row when versioning is
 *  on) and makes progress reportable.
 */
const RESOLVE_BATCH_GROUPS = 40;

/** How many members of a big group are shown before the `+N more` toggle. */
const DUP_COLLAPSE_AT = 10;

/** Group cards rendered per page of the duplicates panel. */
const DUP_PAGE_SIZE = 25;

/** A bulk resolve that failed after some batches had already been applied.
 *
 *  Carries the batch count so the toast can say what survived rather than
 *  reporting a bare error over work that partly succeeded.
 */
class PartialResolveError extends Error {
  // Assigned in the body rather than declared as constructor parameter
  // properties — `erasableSyntaxOnly` is on, and those are not erasable.
  done: number;
  total: number;
  inner: unknown;
  constructor(done: number, total: number, inner: unknown) {
    super("duplicate resolution failed partway through");
    this.name = "PartialResolveError";
    this.done = done;
    this.total = total;
    this.inner = inner;
  }
}

const QUALITY_WORKFLOW_DEFAULTS: QualityWorkflow = {
  runAesthetic: true,
  runTechnical: true,
  runWatermark: false,
  runEmbeddings: false,
  runDino: false,
  runNsfw: false,
  runDinoLayers: false,
  embeddingType: "clip",
  dinoLayer: "all",
};

interface QualityFilters {
  activeSubfolder: string | null;
  showStyleSection: boolean;
  selectedRefIds: string[];
}

const QUALITY_FILTERS_DEFAULTS: QualityFilters = {
  activeSubfolder: null,
  showStyleSection: false,
  selectedRefIds: [],
};

const SCORING_OPTIONS = [
  { key: "aesthetic", label: "Aesthetic score · LAION", desc: "CLIP-based aesthetic predictor (1–10). Trained on human ratings.", vram: "GPU · 2.1 GB" },
  { key: "technical", label: "Technical · OpenCV", desc: "Blur, noise, near-uniform, color, saturation, brightness, pHash duplicates.", vram: "CPU only" },
  { key: "watermark", label: "Watermark detection", desc: "CLIP zero-shot classification for text overlays and logos.", vram: "GPU · 2.1 GB" },
  { key: "embeddings", label: "Style embeddings · CLIP", desc: "Required for the style-similarity workflow below.", vram: "GPU · 2.1 GB" },
  { key: "dino", label: "DINOv2 embeddings", desc: "Object-aware embedding. Can be used alone or alongside CLIP for style similarity.", vram: "GPU · 1.2 GB" },
  { key: "dino_layers", label: "DINOv2 per-layer embeds", desc: "Stores all 12 transformer layer CLS tokens. Enables per-layer style similarity.", vram: "GPU · 1.2 GB" },
  { key: "nsfw", label: "NSFW detection · Marqo", desc: "ViT classifier (Marqo/nsfw-image-detection-384) — sets the is_nsfw quality flag.", vram: "GPU · 1.0 GB" },
];

export default function QualityPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  // Remembered "workflow" config — global, shared across all datasets.
  const [workflow] = useState(() => loadPersisted(QUALITY_WORKFLOW_KEY, QUALITY_WORKFLOW_DEFAULTS));
  const [runAesthetic, setRunAesthetic] = useState(workflow.runAesthetic);
  const [runTechnical, setRunTechnical] = useState(workflow.runTechnical);
  const [runWatermark, setRunWatermark] = useState(workflow.runWatermark);
  const [runEmbeddings, setRunEmbeddings] = useState(workflow.runEmbeddings);
  const [runDino, setRunDino] = useState(workflow.runDino);
  const [runNsfw, setRunNsfw] = useState(workflow.runNsfw);
  const [jobLabel, setJobLabel] = useState("");
  const [runDinoLayers, setRunDinoLayers] = useState(workflow.runDinoLayers);
  const [embeddingType, setEmbeddingType] = useState<"clip" | "dino" | "combined">(workflow.embeddingType);
  const [dinoLayer, setDinoLayer] = useState<number | "all" | null>(workflow.dinoLayer);

  // Remembered "filters" config — per-dataset.
  const [filters] = useState(() =>
    datasetId ? loadPersisted(datasetScopedKey(QUALITY_FILTERS_PREFIX, datasetId), QUALITY_FILTERS_DEFAULTS) : QUALITY_FILTERS_DEFAULTS
  );
  const [showStyleSection, setShowStyleSection] = useState(filters.showStyleSection);
  const [selectedRefIds, setSelectedRefIds] = useState<Set<string>>(new Set(filters.selectedRefIds));
  const [externalRefFiles, setExternalRefFiles] = useState<File[]>([]);
  const [activeSubfolder, setActiveSubfolder] = useState<string | undefined>(filters.activeSubfolder ?? undefined);
  // Pending resolution held for the confirm dialog. Per-group: same-source only
  // — refusing outright would break the legitimate case (two genuinely redundant
  // frames from one shot) and push users to work around it in the gallery, so
  // the fix is a beat of friction, not a block. Bulk: always.
  const [pendingResolve, setPendingResolve] = useState<PendingResolve | null>(null);
  // Duplicates panel triage state — deliberately *not* persisted. It is
  // ephemeral: which slice of groups you are working through right now, reset
  // the moment the panel is refetched. Putting it in the dataset-scoped
  // QUALITY_FILTERS blob would drag in the storage-key registry rules for no
  // benefit.
  const [dupFilter, setDupFilter] = useState<DupFilter>("all");
  const [visibleGroups, setVisibleGroups] = useState(DUP_PAGE_SIZE);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set());
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);

  const { data: subfolders = [] } = useQuery({
    queryKey: ["subfolders", datasetId],
    queryFn: () => datasetsApi.subfolders(datasetId!),
    enabled: !!datasetId,
  });

  useJobSSE(activeJobId);
  const jobProgress = useJobStore((s) => s.activeJobs.get(activeJobId ?? ""));

  const embeddingTypeInitialized = useRef(false);
  useEffect(() => {
    if (!embeddingTypeInitialized.current) { embeddingTypeInitialized.current = true; return; }
    if (embeddingType === "clip") setDinoLayer("all");
  }, [embeddingType]);

  // Persist the "workflow" config (scoring toggles/style settings) — global, debounced.
  useDebouncedPersist(QUALITY_WORKFLOW_KEY, {
    runAesthetic, runTechnical, runWatermark, runEmbeddings, runDino, runNsfw,
    runDinoLayers, embeddingType, dinoLayer,
  });

  // Persist "filters" config (subfolder/style refs) — per-dataset, debounced.
  useDebouncedPersist(
    datasetId ? datasetScopedKey(QUALITY_FILTERS_PREFIX, datasetId) : null,
    {
      activeSubfolder: activeSubfolder ?? null,
      showStyleSection,
      selectedRefIds: [...selectedRefIds],
    },
  );

  // Reload the "filters" blob when datasetId changes without a remount (pane mode).
  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    const next = datasetId
      ? loadPersisted(datasetScopedKey(QUALITY_FILTERS_PREFIX, datasetId), QUALITY_FILTERS_DEFAULTS)
      : QUALITY_FILTERS_DEFAULTS;
    setActiveSubfolder(next.activeSubfolder ?? undefined);
    setShowStyleSection(next.showStyleSection);
    setSelectedRefIds(new Set(next.selectedRefIds));
    // A new dataset is a new duplicates list — start at the top of it. The
    // ephemeral triage state below rides along here rather than in an effect of
    // its own, which would be a second `setState`-in-an-effect for one fact.
    setVisibleGroups(DUP_PAGE_SIZE);
  }, [datasetId]);

  useEffect(() => {
    if (jobProgress?.status === "completed") {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["duplicates", datasetId] });
      setActiveJobId(null);
    }
  }, [jobProgress?.status, datasetId, qc]);

  /* Find last completed quality score job */
  const { data: jobs } = useQuery({
    queryKey: ["jobs"],
    queryFn: async () => {
      const r = await fetch("/api/v1/jobs/?limit=50");
      return r.json() as Promise<Array<{ job_type: string; status: string; finished_at: string | null }>>;
    },
    staleTime: 60_000,
  });
  const lastScoringJob = jobs?.find((j) => j.job_type === "quality_score" && j.status === "completed");
  const lastRunLabel = lastScoringJob?.finished_at
    ? (() => {
        const diff = Date.now() - new Date(lastScoringJob.finished_at).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 60) return `${mins}m ago`;
        return `${Math.floor(mins / 60)}h ago`;
      })()
    : null;

  const { data: duplicates } = useQuery({
    queryKey: ["duplicates", datasetId],
    queryFn: () => qualityApi.duplicates(datasetId!),
    enabled: !!datasetId,
  });

  // The duplicate scan's radius is a setting, so the group header has to read it
  // rather than restate a number: it was hardcoded to "< 6" while the default is
  // 8 and the value is editable in Settings → Thresholds. Same query key and
  // staleTime as StatsPage, which renders the same threshold as a flag hint.
  const { data: thresholds } = useQuery<Thresholds>({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });

  const scoreMutation = useMutation({
    mutationFn: () =>
      qualityApi.score({
        dataset_id: datasetId!,
        subfolder: activeSubfolder,
        run_aesthetic: runAesthetic,
        run_technical: runTechnical,
        run_watermark: runWatermark,
        run_embeddings: runEmbeddings,
        run_dino: runDino,
        run_dino_layers: runDino && runDinoLayers,
        run_nsfw: runNsfw,
        label: jobLabel.trim() || undefined,
      }),
    onSuccess: (data) => {
      if (data.job_id) { setActiveJobId(data.job_id); toast.success("Quality scoring started"); }
    },
    onError: () => toast.error("Failed to start scoring"),
  });

  // One mutation for both paths: a single-group resolve is a plan of one. It
  // walks the plan in batches of RESOLVE_BATCH_GROUPS so a 138-group run is a
  // handful of bounded requests with reportable progress, and a failure halfway
  // reports what already landed instead of a bare error.
  const resolveMutation = useMutation({
    mutationFn: async (plans: DuplicateImage[][]) => {
      setBulkProgress({ done: 0, total: plans.length });
      let done = 0;
      for (let i = 0; i < plans.length; i += RESOLVE_BATCH_GROUPS) {
        const batch = plans.slice(i, i + RESOLVE_BATCH_GROUPS);
        try {
          await qualityApi.resolveDuplicates(
            batch.map((g) => g[0].id),
            batch.flatMap((g) => g.slice(1).map((m) => m.id)),
          );
        } catch (err) {
          throw new PartialResolveError(done, plans.length, err);
        }
        done += batch.length;
        setBulkProgress({ done, total: plans.length });
      }
      return { groups: plans.length, images: plannedDeletions(plans) };
    },
    onSuccess: ({ groups, images }) => {
      toast.success(
        groups === 1
          ? "Duplicates resolved"
          : `Resolved ${groups} groups · ${images} image${images === 1 ? "" : "s"} deleted`,
      );
    },
    onError: (err: unknown) => {
      if (err instanceof PartialResolveError && err.done > 0) {
        toast.error(`Resolved ${err.done} of ${err.total} groups before failing`);
        return;
      }
      const inner = err instanceof PartialResolveError ? err.inner : err;
      toast.error(apiErrorDetail(inner, "Failed to resolve duplicates"));
    },
    // Invalidate on both outcomes: a partial run deleted real rows.
    onSettled: () => {
      setBulkProgress(null);
      qc.invalidateQueries({ queryKey: ["duplicates", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
    },
  });

  const similarityMutation = useMutation({
    mutationFn: async () => {
      let reference_embeddings: string[] = [];
      if (externalRefFiles.length > 0) {
        const result = await qualityApi.embedReferences(externalRefFiles);
        reference_embeddings = result.embeddings;
      }
      const effectiveType = externalRefFiles.length > 0 ? "clip" : embeddingType;
      let apiType: "clip" | "dino" | "combined" | "dino_all_layers" | "combined_all_layers" = effectiveType as typeof apiType;
      if (effectiveType === "dino" && dinoLayer === "all") apiType = "dino_all_layers";
      else if (effectiveType === "combined" && dinoLayer === "all") apiType = "combined_all_layers";
      const effectiveDinoLayer = (["dino", "combined"].includes(effectiveType) && typeof dinoLayer === "number") ? dinoLayer : undefined;
      return qualityApi.styleSimilarity({
        dataset_id: datasetId!,
        reference_image_ids: Array.from(selectedRefIds),
        reference_embeddings,
        embedding_type: apiType,
        dino_layer: effectiveDinoLayer,
      });
    },
    onSuccess: (data) => {
      const skipped = data.skipped ?? 0;
      const msg = skipped > 0
        ? `Style similarity scored for ${data.updated} images (${skipped} skipped — run embeddings first)`
        : `Style similarity scored for ${data.updated} images`;
      toast.success(msg);
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["image"] });
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, err instanceof Error ? err.message : "Style similarity scoring failed"));
    },
  });

  const toggleRef = (id: string) => {
    setSelectedRefIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  function handleResetToDefaults() {
    clearPersisted(QUALITY_WORKFLOW_KEY);
    if (datasetId) clearPersisted(datasetScopedKey(QUALITY_FILTERS_PREFIX, datasetId));

    setRunAesthetic(QUALITY_WORKFLOW_DEFAULTS.runAesthetic);
    setRunTechnical(QUALITY_WORKFLOW_DEFAULTS.runTechnical);
    setRunWatermark(QUALITY_WORKFLOW_DEFAULTS.runWatermark);
    setRunEmbeddings(QUALITY_WORKFLOW_DEFAULTS.runEmbeddings);
    setRunDino(QUALITY_WORKFLOW_DEFAULTS.runDino);
    setRunNsfw(QUALITY_WORKFLOW_DEFAULTS.runNsfw);
    setRunDinoLayers(QUALITY_WORKFLOW_DEFAULTS.runDinoLayers);
    setEmbeddingType(QUALITY_WORKFLOW_DEFAULTS.embeddingType);
    setDinoLayer(QUALITY_WORKFLOW_DEFAULTS.dinoLayer);

    setActiveSubfolder(QUALITY_FILTERS_DEFAULTS.activeSubfolder ?? undefined);
    setShowStyleSection(QUALITY_FILTERS_DEFAULTS.showStyleSection);
    setSelectedRefIds(new Set(QUALITY_FILTERS_DEFAULTS.selectedRefIds));
    setExternalRefFiles([]);

    toast.success("Configuration reset to defaults");
  }

  // Per-group facts derived once per payload instead of inside the render map.
  const groupMeta = useMemo<DupGroupMeta[]>(
    () => (duplicates?.groups ?? []).map((group) => ({
      group,
      rootId: group[0].id,
      shared: sharedSourceVideo(group),
      anyScored: group.some((m) => m.aesthetic_score != null),
      anyLineage: group.some((m) => m.source_video_id != null),
    })),
    [duplicates],
  );
  const videoGroupCount = groupMeta.filter((g) => g.shared != null).length;

  // The chip row hides when no group is same-source, so a filter left on "video"
  // after switching datasets would strand an empty list with no way back. Fixed
  // by *deriving* the filter rather than correcting the stored one in an effect:
  // there is then no render in which the panel is filtered by a chip it is not
  // showing. `dupFilter` keeps its value for when video groups reappear.
  const activeDupFilter: DupFilter = videoGroupCount === 0 ? "all" : dupFilter;

  const filteredGroups = useMemo(
    () => (activeDupFilter === "all"
      ? groupMeta
      : groupMeta.filter((g) => (activeDupFilter === "video" ? g.shared != null : g.shared == null))),
    [groupMeta, activeDupFilter],
  );

  // The bulk plans, over the *filtered* set — every matching group, not just the
  // ones paged into view. Same superset trap as gallery select-all: the button
  // label and the confirm dialog both have to state this count, never the
  // rendered one.
  const bulkPlans = useMemo(() => {
    // *Keep best* skips a group with nothing scored rather than falling back to
    // first — "best" is meaningless there, and silently keeping whichever image
    // came first and calling it best is the bug rankForKeepBest exists to fix.
    const scored = filteredGroups.filter((g) => g.anyScored);
    return {
      best: scored.map((g) => rankForKeepBest(g.group)),
      bestSameSource: scored.filter((g) => g.shared != null).length,
      skipped: filteredGroups.length - scored.length,
      first: filteredGroups.map((g) => g.group),
      firstSameSource: filteredGroups.filter((g) => g.shared != null).length,
    };
  }, [filteredGroups]);

  /** Switch chip. A new chip is a new list, so paging restarts with it. */
  const pickDupFilter = (next: DupFilter) => {
    setDupFilter(next);
    setVisibleGroups(DUP_PAGE_SIZE);
  };

  const resolveBusy = resolveMutation.isPending;
  const bulkLabel = (base: string) =>
    (resolveBusy && bulkProgress ? `Resolving ${bulkProgress.done}/${bulkProgress.total}…` : base);

  /** Queue a bulk run over every group matching the active chip. */
  const startBulk = (mode: "best" | "first") => {
    const plans = mode === "best" ? bulkPlans.best : bulkPlans.first;
    if (plans.length === 0) return;
    setPendingResolve({
      kind: "bulk",
      mode,
      plans,
      sameSource: mode === "best" ? bulkPlans.bestSameSource : bulkPlans.firstSameSource,
      skipped: mode === "best" ? bulkPlans.skipped : 0,
    });
  };

  /** Resolve a single group. Both buttons route through here: one ordered array
   *  yields the survivor, the deletions and their timestamps, so `plans` and
   *  `timestamps` cannot desynchronise the way two independent `.slice(1)`
   *  passes could. */
  const resolveGroup = (meta: DupGroupMeta, mode: "best" | "first") => {
    const ordered = mode === "best" ? rankForKeepBest(meta.group) : meta.group;
    if (meta.shared) {
      setPendingResolve({
        kind: "group",
        mode,
        plans: [ordered],
        videoName: meta.shared.name,
        timestamps: ordered.slice(1).map((i) => i.source_timestamp_ms),
      });
    } else {
      resolveMutation.mutate([ordered]);
    }
  };

  const toggleGroupExpanded = (rootId: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(rootId)) next.delete(rootId); else next.add(rootId);
      return next;
    });
  };

  const isRunning = scoreMutation.isPending || jobProgress?.status === "running";
  const checkMap: Record<string, [boolean, (v: boolean) => void]> = {
    aesthetic: [runAesthetic, setRunAesthetic],
    technical: [runTechnical, setRunTechnical],
    watermark: [runWatermark, setRunWatermark],
    embeddings: [runEmbeddings, setRunEmbeddings],
    dino: [runDino, setRunDino],
    dino_layers: [runDinoLayers, setRunDinoLayers],
    nsfw: [runNsfw, setRunNsfw],
  };

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h">
        <div>
          <h1>Score images</h1>
          <p>Run aesthetic, technical, watermark and embedding analysis on the dataset.</p>
        </div>
      </div>

      {/* Run scoring panel */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h">
          {lastRunLabel && (
            <span className="badge dot info">Last run · {lastRunLabel}</span>
          )}
          <h3 style={{ marginLeft: lastRunLabel ? 12 : 0 }}>Run quality analysis</h3>
          <div style={{ flex: 1 }} />
          <button className="btn ghost sm" onClick={handleResetToDefaults} title="Clear remembered configuration and revert to defaults">
            Reset to defaults
          </button>
          {subfolders.some((sf) => sf.path) && (
            <select
              className="select"
              value={activeSubfolder ?? ""}
              onChange={(e) => setActiveSubfolder(e.target.value === "" ? undefined : e.target.value)}
              style={{ fontSize: 12, height: 30, marginRight: 8 }}
              disabled={isRunning}
            >
              <option value="">All subfolders</option>
              {subfolders.filter((sf) => sf.path).map((sf) => (
                <option key={sf.path} value={sf.path}>{sf.path} ({sf.image_count})</option>
              ))}
            </select>
          )}
          <input
            className="input"
            type="text"
            placeholder="Job label (optional)"
            value={jobLabel}
            onChange={(e) => setJobLabel(e.target.value)}
            style={{ width: 180, fontSize: 12 }}
            title="Optional name shown in the job queue"
          />
          <button className="btn primary" onClick={() => scoreMutation.mutate()} disabled={isRunning}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M2.5 8a5.5 5.5 0 1010-2"/><path d="M11 3.5l1.5 2.5L10 7"/>
            </svg>
            {isRunning ? "Scoring…" : "Run scoring"}
          </button>
        </div>
        <div className="panel-b">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {SCORING_OPTIONS.filter((o) => {
              if (o.key === "dino_layers") return runDino;
              return true;
            }).map((opt) => {
              const [checked, setChecked] = checkMap[opt.key];
              return (
                <label key={opt.key} className={`model-row${checked ? " sel" : ""}`} style={{ cursor: "pointer" }}>
                  <input type="checkbox" className="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} style={{ marginRight: 4 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="mr-name">{opt.label}</div>
                    <div className="mr-desc">{opt.desc}</div>
                  </div>
                  <span className="mr-vram">{opt.vram}</span>
                </label>
              );
            })}
          </div>

          {/* Progress */}
          {jobProgress && (
            <div style={{ marginTop: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--fg-mute)", marginBottom: 6 }}>
                <span>{jobProgress.message}</span>
                <span className="mono">{jobProgress.done}/{jobProgress.total}</span>
              </div>
              <div style={{ height: 5, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${jobProgress.percent ?? 0}%`, background: "linear-gradient(90deg, var(--accent-2), var(--accent))", transition: "width .4s" }} />
              </div>
              {jobProgress.status === "completed" && <p style={{ color: "var(--good)", fontSize: 12, marginTop: 6 }}>✓ Scoring complete</p>}
            </div>
          )}
        </div>
      </div>

      {/* Style similarity panel */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-h">
          <h3>Style similarity</h3>
          <div style={{ flex: 1 }} />
          <span className="mono" style={{ color: "var(--fg-dim)", fontSize: 11 }}>Cosine similarity to reference embeddings</span>
          <button className="icon-btn" onClick={() => setShowStyleSection((v) => !v)}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d={showStyleSection ? "M3 10l5-5 5 5" : "M3 6l5 5 5-5"}/>
            </svg>
          </button>
        </div>

        {showStyleSection && (
          <div style={{ padding: "4px 22px" }}>
            <div className="form-row">
              <div className="lbl-col">
                <h4>Embedding model</h4>
                <p>CLIP for general images; DINOv2 for object-shape similarity; CLIP + DINOv2 blends both (0.38 × CLIP + 0.62 × DINOv2). All require embeddings computed first.</p>
              </div>
              <div className="row-flex">
                <button className={`btn sm${embeddingType === "clip" ? " primary" : ""}`} onClick={() => setEmbeddingType("clip")}>CLIP</button>
                <button className={`btn sm${embeddingType === "dino" ? " primary" : ""}`} onClick={() => setEmbeddingType("dino")} disabled={externalRefFiles.length > 0}>DINOv2</button>
                <button className={`btn sm${embeddingType === "combined" ? " primary" : ""}`} onClick={() => setEmbeddingType("combined")} disabled={externalRefFiles.length > 0}>CLIP + DINOv2</button>
              </div>
            </div>

            {(embeddingType === "dino" || embeddingType === "combined") && externalRefFiles.length === 0 && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>DINOv2 layer</h4>
                  <p>Each transformer block captures increasingly abstract features. "Final" uses the pre-computed <span className="mono">dino_embedding</span>. All others require per-layer embeddings. "All layers" scores each layer independently and stores results for comparison in the image detail view.</p>
                </div>
                <select
                  className="select"
                  value={dinoLayer === "all" ? "all" : (dinoLayer ?? 12)}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "all") setDinoLayer("all");
                    else { const n = Number(v); setDinoLayer(n === 12 ? null : n); }
                  }}
                >
                  {Array.from({ length: 12 }, (_, i) => i + 1).map((n) => (
                    <option key={n} value={n}>Layer {n} — {DINO_LAYER_LABELS[String(n)]}</option>
                  ))}
                  <option value="all">{embeddingType === "combined" ? "All layers — Score CLIP + each DINOv2 layer individually" : "All layers — Score each layer individually"}</option>
                </select>
              </div>
            )}

            <div className="form-row">
              <div className="lbl-col">
                <h4>Reference images</h4>
                <p>Pick from the dataset, or drag in local files (always embedded with CLIP).</p>
              </div>
              {datasetId && (
                <StyleReferencePicker
                  datasetId={datasetId}
                  selectedIds={selectedRefIds}
                  onToggle={toggleRef}
                  externalFiles={externalRefFiles}
                  onExternalFilesChange={setExternalRefFiles}
                />
              )}
            </div>

            <div className="form-row">
              <div className="lbl-col">
                <h4>Action</h4>
                <p>Score writes <span className="mono">style_similarity_score</span> per image. CPU-only, runs immediately.</p>
              </div>
              <button
                className="btn primary"
                onClick={() => similarityMutation.mutate()}
                disabled={(selectedRefIds.size === 0 && externalRefFiles.length === 0) || similarityMutation.isPending}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <path d="M2.5 8a5.5 5.5 0 1010-2"/><path d="M11 3.5l1.5 2.5L10 7"/>
                </svg>
                Score similarity
                {(selectedRefIds.size + externalRefFiles.length) > 0 && ` · ${selectedRefIds.size + externalRefFiles.length} refs`}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Duplicates */}
      {groupMeta.length > 0 && (
        <div className="panel">
          <div className="panel-h">
            <h3>Duplicate groups</h3>
            <span className="badge warn dot">{groupMeta.length} groups</span>
          </div>
          <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {/* Triage bar. The chips scope what is *shown*; the bulk buttons act
                on everything the active chip matches — including the groups
                below the fold — so their counts read from `filteredGroups` and
                never from the rendered slice. */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              {/* Only worth showing when the partition is non-trivial; with no
                  same-source group the three chips are noise. */}
              {videoGroupCount > 0 && (
                <div style={{ display: "flex", gap: 6 }}>
                  <button className={`btn sm${activeDupFilter === "all" ? " primary" : ""}`} onClick={() => pickDupFilter("all")}>
                    All ({groupMeta.length})
                  </button>
                  <button className={`btn sm${activeDupFilter === "video" ? " primary" : ""}`} onClick={() => pickDupFilter("video")}>
                    From one video ({videoGroupCount})
                  </button>
                  <button className={`btn sm${activeDupFilter === "other" ? " primary" : ""}`} onClick={() => pickDupFilter("other")}>
                    Mixed or no video ({groupMeta.length - videoGroupCount})
                  </button>
                </div>
              )}
              <div style={{ flex: 1 }} />
              <button
                className="btn sm primary"
                disabled={resolveBusy || bulkPlans.best.length === 0}
                title={
                  bulkPlans.best.length === 0
                    ? "No image in these groups has an aesthetic score — run scoring first, or use Keep first."
                    : bulkPlans.skipped > 0
                      ? `Skips ${bulkPlans.skipped} group${bulkPlans.skipped === 1 ? "" : "s"} with no aesthetic score — there is no best to keep there.`
                      : undefined
                }
                onClick={() => startBulk("best")}
              >
                {bulkLabel(`Keep best in ${bulkPlans.best.length} group${bulkPlans.best.length === 1 ? "" : "s"}`)}
              </button>
              <button
                className="btn sm"
                disabled={resolveBusy || bulkPlans.first.length === 0}
                title="Keeps the image the duplicate scan picked in every group shown by the active filter."
                onClick={() => startBulk("first")}
              >
                {bulkLabel(`Keep first in ${bulkPlans.first.length} group${bulkPlans.first.length === 1 ? "" : "s"}`)}
              </button>
            </div>

            {filteredGroups.length === 0 && (
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "6px 0" }}>
                No duplicate group matches this filter.
              </p>
            )}

            {filteredGroups.slice(0, visibleGroups).map((meta) => {
              const { group, rootId, shared, anyScored, anyLineage } = meta;
              // A 36-member group is unreadable and expensive; show the first
              // ten behind a toggle. `group[0]` — the image the scan kept, and
              // the one every resolution keeps — is always in that slice.
              const expanded = expandedGroups.has(rootId);
              const shown = expanded ? group : group.slice(0, DUP_COLLAPSE_AT);
              return (
              // `minmax(0, 1fr)`, not `1fr`: a bare `1fr` track refuses to shrink
              // below its content, so a wide group pushed the action column off
              // the right edge of the pane — unreachable exactly when the group
              // was biggest.
              <div key={rootId} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 14, alignItems: "center", padding: 12, background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
                <div>
                  <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 8 }}>
                    {group.length} similar images
                    {thresholds && ` · perceptual hash distance < ${thresholds.duplicate_threshold}`}
                  </div>
                  {/* Same-source banner. A perceptual hash cannot tell a held
                      animation cel or recycled footage from a redundant copy, so
                      say where these came from before anything is deleted. */}
                  {shared && (
                    <div
                      style={{
                        marginBottom: 8, padding: "6px 9px", borderRadius: "var(--r-sm)",
                        background: "rgba(210,154,58,.10)", border: "1px solid rgba(210,154,58,.35)",
                        fontSize: 11.5, color: "var(--warn)",
                      }}
                    >
                      All {group.length} frames came from{" "}
                      <span className="mono">{shared.name ?? "a video that has since been deleted"}</span>.
                      Held animation cels and recycled footage look identical to a perceptual hash —
                      check the timestamps before deleting.
                    </div>
                  )}
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-start" }}>
                    {shown.map((img) => (
                      <div key={img.id} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                        <img
                          src={imagesApi.thumbnailUrlVersioned(img.id, img.updated_at)}
                          alt={img.filename}
                          loading="lazy"
                          title={img.kept ? "Kept by the scan — the other copies point at this one" : undefined}
                          style={{ width: 64, height: 64, objectFit: "cover", borderRadius: "var(--r-sm)", border: img.kept ? "2px solid var(--good)" : "1px solid var(--line-2)" }}
                        />
                        <span className="mono" style={{ fontSize: 10, color: img.kept ? "var(--good)" : "var(--fg-dim)", textAlign: "center", maxWidth: 64, overflow: "hidden", textOverflow: "ellipsis" }}>{img.filename}</span>
                        {img.kept && <span style={{ fontSize: 10, color: "var(--good)" }}>kept</span>}
                        {/* A mixed group names the video per thumbnail — the
                            banner above would be false there. */}
                        {!shared && anyLineage && img.source_video_id && (
                          <span className="mono" style={{ fontSize: 9.5, color: "var(--warn)", textAlign: "center", maxWidth: 64, overflow: "hidden", textOverflow: "ellipsis" }} title={img.source_video_name ?? undefined}>
                            {img.source_video_name ?? "video"}
                          </span>
                        )}
                        {img.source_timestamp_ms != null && (
                          <span className="mono" style={{ fontSize: 9.5, color: "var(--fg-dim)" }}>
                            {formatFramePosition(img.source_timestamp_ms)}
                            {img.source_shot_index != null && ` · shot ${img.source_shot_index}`}
                          </span>
                        )}
                        {img.aesthetic_score != null && <span className="mono" style={{ fontSize: 11, color: "var(--good)" }}>{img.aesthetic_score.toFixed(1)}</span>}
                      </div>
                    ))}
                    {group.length > DUP_COLLAPSE_AT && (
                      <button
                        className="btn sm"
                        style={{ width: 64, height: 64, padding: 4, fontSize: 11, lineHeight: 1.25, whiteSpace: "normal" }}
                        onClick={() => toggleGroupExpanded(rootId)}
                      >
                        {expanded ? "Show fewer" : `+${group.length - DUP_COLLAPSE_AT} more`}
                      </button>
                    )}
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <button
                    className="btn sm primary"
                    // "Best" is meaningless with nothing scored: the old code
                    // silently kept whichever member came first and called it
                    // best. Say so instead of pretending.
                    disabled={!anyScored || resolveBusy}
                    title={anyScored ? undefined : "No image in this group has an aesthetic score — run scoring first, or use Keep first."}
                    onClick={() => resolveGroup(meta, "best")}
                  >Keep best</button>
                  {/* group[0] is the image the scan kept — see get_duplicates.
                      Confirms on a same-source group just as *Keep best* does:
                      it deletes the same N−1 frames on one click, and the hazard
                      belongs to the group, not to the ranking. */}
                  <button className="btn sm" disabled={resolveBusy} onClick={() => resolveGroup(meta, "first")}>Keep first</button>
                </div>
              </div>
              );
            })}

            {filteredGroups.length > visibleGroups && (
              <button
                className="btn sm"
                style={{ alignSelf: "center" }}
                onClick={() => setVisibleGroups((n) => n + DUP_PAGE_SIZE)}
              >
                Show {Math.min(DUP_PAGE_SIZE, filteredGroups.length - visibleGroups)} more
                {" "}({filteredGroups.length - visibleGroups} remaining)
              </button>
            )}
          </div>
        </div>
      )}

      {pendingResolve && (
        <ConfirmDialog
          {...resolveConfirmCopy(pendingResolve)}
          confirmLabel={
            pendingResolve.kind === "group"
              ? "Delete frames"
              : `Delete ${plannedDeletions(pendingResolve.plans)} images`
          }
          danger
          onCancel={() => setPendingResolve(null)}
          onConfirm={() => {
            resolveMutation.mutate(pendingResolve.plans);
            setPendingResolve(null);
          }}
        />
      )}
    </div>
  );
}
