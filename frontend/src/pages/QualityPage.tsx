import { useState, useEffect, useMemo, useRef } from "react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../utils/apiError";
import { qualityApi, type DuplicateGroup, type DuplicateImage } from "../api/quality";
import ConfirmDialog from "../components/common/ConfirmDialog";
import { formatFramePosition, formatTimeAgo } from "../utils/duration";
import { settingsApi, type Thresholds } from "../api/settings";
import { datasetsApi } from "../api/datasets";
import { imagesApi } from "../api/images";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import StyleReferencePicker from "../components/quality/StyleReferencePicker";
import { DINO_LAYER_LABELS } from "../constants/dinoLabels";
import { STYLE_MODES, STYLE_MODE_NOTE, DINO_LAYER_NOTE, styleModeLabel, type StyleMode } from "../constants/styleModes";
import { AESTHETIC_MODELS, aestheticModelLabel, type AestheticModel } from "../constants/aestheticModels";
import { QUALITY_WORKFLOW_KEY, QUALITY_FILTERS_PREFIX } from "../constants/storage";
import { loadPersisted, clearPersisted, datasetScopedKey } from "../utils/persistentState";
import { invalidateDatasetContentScope } from "../constants/queryKeys";
import { useDebouncedPersist } from "../hooks/useDebouncedPersist";
import { useStyleDistribution } from "../hooks/useStyleDistribution";
import { quantileAt } from "../utils/percentile";

interface QualityWorkflow {
  runAesthetic: boolean;
  /** Which model writes `aesthetic_score`. A per-run choice with a sticky
   *  default, riding in this already-global blob beside `embeddingType` — no DB
   *  setting and no per-dataset override, because it is a property of the run,
   *  not of the dataset. */
  aestheticModel: AestheticModel;
  runTechnical: boolean;
  runWatermark: boolean;
  runEmbeddings: boolean;
  runDino: boolean;
  runNsfw: boolean;
  runDinoLayers: boolean;
  embeddingType: StyleMode;
  dinoLayer: number | "all" | null;
}

/** The loader's own VRAM figure, rendered the way the captioning model list
 *  renders it. Local rather than shared: it is one line, and the figure is an
 *  estimate the button only has to make legible. */
function formatVram(mb: number): string {
  return `${(mb / 1024).toFixed(1)} GB`;
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
  // Deliberately unaware of `aesthetic_model`: this is a pure, null-safe sort
  // and it is correct *within* one scale. Ranking across two incomparable
  // scales is refused at the caller, where the unscored refusal already lives.
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
  /** Two or more distinct `aesthetic_model` markers among the group's scored
   *  members. *Keep best* must refuse: it ranks by `aesthetic_score` and then
   *  **deletes** everything below the top, so comparing a LAION 6.2 against a
   *  V2.5 6.2 destroys images on a number that means two different things. */
  mixedModels: boolean;
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
      skippedMixed: number;
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
  if (p.skippedMixed > 0) {
    message +=
      ` ${p.skippedMixed} group${p.skippedMixed === 1 ? "" : "s"} ` +
      `${p.skippedMixed === 1 ? "is" : "are"} skipped for holding scores from two different ` +
      `aesthetic models, whose scales are not comparable. Re-score ${p.skippedMixed === 1 ? "it" : "them"} ` +
      `with one model to include ${p.skippedMixed === 1 ? "it" : "them"}.`;
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
  aestheticModel: "laion",
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
  // No model in the label: the producer is a choice now, made in the sub-row
  // below the grid rather than baked into the checkbox's name.
  { key: "aesthetic", label: "Aesthetic score", desc: "Learned aesthetic predictor (1–10). Trained on human ratings.", vram: "GPU · 2.1 GB" },
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
  const [aestheticModel, setAestheticModel] = useState<AestheticModel>(workflow.aestheticModel);
  const [runTechnical, setRunTechnical] = useState(workflow.runTechnical);
  const [runWatermark, setRunWatermark] = useState(workflow.runWatermark);
  const [runEmbeddings, setRunEmbeddings] = useState(workflow.runEmbeddings);
  const [runDino, setRunDino] = useState(workflow.runDino);
  const [runNsfw, setRunNsfw] = useState(workflow.runNsfw);
  const [jobLabel, setJobLabel] = useState("");
  const [runDinoLayers, setRunDinoLayers] = useState(workflow.runDinoLayers);
  const [embeddingType, setEmbeddingType] = useState<StyleMode>(workflow.embeddingType);
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
  // No new storage key: `loadPersisted`'s shallow merge onto the defaults means
  // a blob written before `aestheticModel` existed still reads back with it.
  useDebouncedPersist(QUALITY_WORKFLOW_KEY, {
    runAesthetic, aestheticModel, runTechnical, runWatermark, runEmbeddings, runDino, runNsfw,
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
      qc.invalidateQueries({ queryKey: ["aesthetic-coverage", datasetId] });
      // The run may have auto-unloaded what it loaded (Settings → Quality), so
      // the Unload button has to re-read residency rather than assume it.
      qc.invalidateQueries({ queryKey: ["quality", "models"] });
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
  // `formatTimeAgo` rather than the inline copy this used to hold — it is the same
  // arithmetic, and it also fixes the bare-timestamp timezone read described there.
  const lastRunLabel = lastScoringJob?.finished_at ? formatTimeAgo(lastScoringJob.finished_at) : null;

  // The style-score distribution behind the panel's "Current scores" line. Same
  // query key as the gallery meter's, so the two share one cache entry.
  const styleDistribution = useStyleDistribution(datasetId);

  const { data: duplicates } = useQuery({
    queryKey: ["duplicates", datasetId],
    queryFn: () => qualityApi.duplicates(datasetId!),
    enabled: !!datasetId,
  });

  // Per-model aesthetic coverage, in this page's own subfolder scope. Its own
  // endpoint rather than a field on the Stats aggregation: it has to follow the
  // scope select and refetch on every finished scoring job, and this page
  // otherwise makes three light queries.
  const { data: aestheticCoverage } = useQuery({
    queryKey: ["aesthetic-coverage", datasetId, activeSubfolder ?? null],
    queryFn: () => qualityApi.aestheticCoverage(datasetId!, activeSubfolder),
    enabled: !!datasetId,
  });
  const coverageByModel = Object.entries(aestheticCoverage?.by_model ?? {});
  // Rows another model already scored — exactly what the re-score offer targets,
  // and the same predicate the server applies for `only_mismatched`.
  const mismatchedCount = coverageByModel
    .filter(([marker]) => marker !== aestheticModel)
    .reduce((n, [, count]) => n + count, 0);

  // The duplicate scan's radius is a setting, so the group header has to read it
  // rather than restate a number: it was hardcoded to "< 6" while the default is
  // 8 and the value is editable in Settings → Thresholds. Same query key and
  // staleTime as StatsPage, which renders the same threshold as a flag hint.
  const { data: thresholds } = useQuery<Thresholds>({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });

  // `onlyMismatched` is the re-score offer: same run, narrowed selection. A
  // mutation variable rather than a second mutation, so the two buttons cannot
  // drift on which checks they run or which model they run them with.
  const scoreMutation = useMutation({
    mutationFn: ({ onlyMismatched = false }: { onlyMismatched?: boolean } = {}) =>
      qualityApi.score({
        dataset_id: datasetId!,
        subfolder: activeSubfolder,
        run_aesthetic: runAesthetic,
        aesthetic_model: aestheticModel,
        only_mismatched: onlyMismatched || undefined,
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
      // `{job_id: null}` when the scope matched nothing — the ordinary answer for
      // a re-score offer whose mismatch count raced to zero.
      else toast(data.message ?? "No images matched");
    },
    onError: () => toast.error("Failed to start scoring"),
  });

  // Which scoring models are resident right now. Global rather than
  // dataset-scoped — VRAM residency is a property of the process — and short
  // `staleTime` because a run on the other side of the app changes it.
  const { data: scoringModels } = useQuery({
    queryKey: ["quality", "models"],
    queryFn: qualityApi.listModels,
    staleTime: 10_000,
  });
  const loadedModels = scoringModels?.filter((m) => m.loaded) ?? [];
  const loadedVramMb = loadedModels.reduce((sum, m) => sum + m.vram_mb, 0);

  const unloadMutation = useMutation({
    mutationFn: qualityApi.unloadModels,
    onSuccess: (data) => {
      toast.success(
        data.unloaded.length
          ? `Freed ${formatVram(data.freed_mb)} of VRAM`
          : "No scoring models were loaded",
      );
      qc.invalidateQueries({ queryKey: ["quality", "models"] });
    },
    onError: () => toast.error("Failed to unload models"),
  });

  // One mutation for both paths: a single-group resolve is a plan of one. It
  // walks the plan in batches of RESOLVE_BATCH_GROUPS so a 138-group run is a
  // handful of bounded requests with reportable progress, and a failure halfway
  // reports what already landed instead of a bare error.
  //
  // `bulk` says who started the run, and only a bulk run writes `bulkProgress`:
  // the label it drives sits on the two top-row buttons, so a single card's
  // *Keep first* relabelling them `Resolving 0/1…` was a run claiming progress
  // it does not own. Per-group buttons stay disabled by `resolveBusy`, which is
  // a genuine busy state either way.
  const resolveMutation = useMutation({
    mutationFn: async ({ plans, bulk }: { plans: DuplicateImage[][]; bulk: boolean }) => {
      if (bulk) setBulkProgress({ done: 0, total: plans.length });
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
        if (bulk) setBulkProgress({ done, total: plans.length });
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
    // Invalidate on both outcomes: a partial run deleted real rows. A bulk run
    // deletes hundreds of them, so the dataset counters and the four stats
    // queries are exactly as stale as the gallery list — hence the shared scope
    // rather than the lone `["images"]` this carried when it resolved one group.
    onSettled: () => {
      setBulkProgress(null);
      qc.invalidateQueries({ queryKey: ["duplicates", datasetId] });
      invalidateDatasetContentScope(qc, datasetId);
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
      // The lone `["images", datasetId]` this replaces left two surfaces stale: the
      // Stats page's style histogram, which reads `["score-values"]` and so sat on
      // pre-run numbers until something else invalidated it, and the new
      // distribution the gallery meter and the detail block both read — that one
      // now rides the shared scope, since it is an aggregate over the same rows.
      invalidateDatasetContentScope(qc, datasetId);
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
    setAestheticModel(QUALITY_WORKFLOW_DEFAULTS.aestheticModel);
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
      mixedModels:
        new Set(group.map((m) => m.aesthetic_model).filter(Boolean)).size > 1,
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
    // A mixed-model group is skipped, not a reason to kill the whole bar:
    // disabling *Keep best* because 1 of 300 groups holds two scales would push
    // users to resolve by hand in the gallery — the same argument the
    // same-source confirm dialog makes. Counted separately from `skipped` so the
    // dialog can say which reason applied. *Keep first* reads no score and is
    // untouched.
    const rankable = scored.filter((g) => !g.mixedModels);
    return {
      best: rankable.map((g) => rankForKeepBest(g.group)),
      bestSameSource: rankable.filter((g) => g.shared != null).length,
      skipped: filteredGroups.length - scored.length,
      skippedMixed: scored.length - rankable.length,
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
      skippedMixed: mode === "best" ? bulkPlans.skippedMixed : 0,
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
      resolveMutation.mutate({ plans: [ordered], bulk: false });
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
          {loadedModels.length > 0 && (
            <button
              className="btn ghost sm"
              // Disabled while a run is in flight: nothing but the single-worker
              // job queue stands between an unload and a scorer mid-batch
              // (`ModelEntry.in_use` is never set).
              disabled={isRunning || unloadMutation.isPending}
              onClick={() => unloadMutation.mutate()}
              title={`Free VRAM held by: ${loadedModels.map((m) => m.name).join(", ")}`}
            >
              {unloadMutation.isPending
                ? "Unloading…"
                : `Unload models · ${formatVram(loadedVramMb)}`}
            </button>
          )}
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
          <button className="btn primary" onClick={() => scoreMutation.mutate({})} disabled={isRunning}>
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

          {/* The aesthetic model picker — a sub-row *under* the grid, not a
              control inside the aesthetic row. That row is a `<label>` wrapping
              its checkbox with `cursor: pointer`, so a select nested in it would
              toggle the checkbox on every click, and `.model-row` is a tight flex
              row in a two-column grid with no space for a per-model VRAM figure.
              The DINOv2-layer select makes exactly this move.

              Deliberately not a `<label>`: the e2e `scorer()` locator matches
              `label` by text, and a second one containing "Aesthetic" would make
              it ambiguous. */}
          {runAesthetic && (
            <div
              style={{
                marginTop: 8, padding: "10px 12px", background: "var(--surface-2)",
                border: "1px solid var(--line)", borderRadius: "var(--r-sm)",
                display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
              }}
            >
              <div style={{ flex: 1, minWidth: 240 }}>
                <div className="mr-name">Aesthetic model</div>
                <div className="mr-desc">
                  {AESTHETIC_MODELS.find((m) => m.value === aestheticModel)?.desc}
                  {" "}The two scales are <strong>not</strong> comparable — every score is
                  stored with the model that made it.
                </div>
              </div>
              <select
                className="select"
                aria-label="Aesthetic model"
                value={aestheticModel}
                onChange={(e) => setAestheticModel(e.target.value as AestheticModel)}
                disabled={isRunning}
                style={{ fontSize: 12 }}
              >
                {AESTHETIC_MODELS.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
              <span className="mr-vram">
                {AESTHETIC_MODELS.find((m) => m.value === aestheticModel)?.vram}
              </span>
            </div>
          )}

          {/* Per-model coverage in the active scope, plus the re-score offer.
              Two literal buttons rather than one with a mode: *Run scoring*
              covers never-scored images, this covers rows another model
              measured. */}
          {runAesthetic && aestheticCoverage && coverageByModel.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--fg-mute)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span>
                Aesthetic coverage: {aestheticCoverage.scored} scored
                {aestheticCoverage.unscored > 0 && `, ${aestheticCoverage.unscored} unscored`}
                {" — "}
                {coverageByModel
                  .map(([marker, count]) => `${count} by ${aestheticModelLabel(marker)}`)
                  .join(", ")}
              </span>
              {mismatchedCount > 0 && (
                <button
                  className="btn sm"
                  disabled={isRunning}
                  onClick={() => scoreMutation.mutate({ onlyMismatched: true })}
                  title={
                    `Re-scores only the ${mismatchedCount} image${mismatchedCount === 1 ? "" : "s"} ` +
                    `in this scope scored by a different model. Never-scored images are not included — ` +
                    `use Run scoring for those.`
                  }
                >
                  Re-score {mismatchedCount} with {AESTHETIC_MODELS.find((m) => m.value === aestheticModel)?.label}
                </button>
              )}
            </div>
          )}

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
                <p>{STYLE_MODES.find((m) => m.value === embeddingType)?.desc} {STYLE_MODE_NOTE}</p>
              </div>
              <div className="row-flex">
                {STYLE_MODES.map((m) => (
                  <button
                    key={m.value}
                    className={`btn sm${embeddingType === m.value ? " primary" : ""}`}
                    onClick={() => setEmbeddingType(m.value)}
                    disabled={m.value !== "clip" && externalRefFiles.length > 0}
                    title={m.desc}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>

            {(embeddingType === "dino" || embeddingType === "combined") && externalRefFiles.length === 0 && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>DINOv2 layer</h4>
                  <p>{DINO_LAYER_NOTE} Layer 12 uses the pre-computed <span className="mono">dino_embedding</span>; every other layer requires per-layer embeddings.</p>
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
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
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
                {/* The picker's selection is React state and the ids are never rendered, so
                    an offline harness has no way to receive them. Dataset refs only —
                    dragged-in local files have no image id to copy. */}
                <button
                  className="btn"
                  onClick={() => {
                    const ids = Array.from(selectedRefIds).join(",");
                    navigator.clipboard.writeText(ids)
                      .then(() => toast.success(`Copied ${selectedRefIds.size} reference ID${selectedRefIds.size === 1 ? "" : "s"}`))
                      .catch(() => toast.error("Could not copy to the clipboard"));
                  }}
                  disabled={selectedRefIds.size === 0}
                  title="Copy the selected dataset reference image IDs as a comma-separated list"
                >
                  Copy reference IDs
                </button>
              </div>
            </div>

            {/* What the last run actually produced, read from the refetched
                distribution rather than from the POST response — the endpoint
                returns counts, and the two numbers worth seeing after a run are
                where the middle of the dataset landed and where the good end
                starts. The raw cosine's scale differs per mode, so these are the
                only figures that let one run be compared against the last. */}
            {styleDistribution && styleDistribution.scored > 1 && (
              <div className="form-row">
                <div className="lbl-col">
                  <h4>Current scores</h4>
                  <p>Across the {styleDistribution.scored} scored image{styleDistribution.scored === 1 ? "" : "s"} in this dataset.
                    {styleDistribution.run && ` Last scored with ${styleModeLabel(styleDistribution.run.embedding_type)}${styleDistribution.run.dino_layer != null ? `, layer ${styleDistribution.run.dino_layer}` : ""} ${formatTimeAgo(styleDistribution.run.updated_at)}.`}</p>
                </div>
                <div style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
                  {/* Breakpoints by percentile, never by index: `quantileAt`
                      derives the index from the payload's own `quantile_step`, so
                      a change to `STYLE_QUANTILE_STEP` cannot relabel the max as
                      the median here. */}
                  <span>Median <span className="mono" style={{ color: "var(--fg)" }}>{quantileAt(styleDistribution, 50)?.toFixed(4)}</span></span>
                  <span>Top 10% above <span className="mono" style={{ color: "var(--fg)" }}>{quantileAt(styleDistribution, 90)?.toFixed(4)}</span></span>
                  <span>Range <span className="mono" style={{ color: "var(--fg)" }}>{quantileAt(styleDistribution, 0)?.toFixed(4)}–{quantileAt(styleDistribution, 100)?.toFixed(4)}</span></span>
                </div>
              </div>
            )}
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
                title={[
                  bulkPlans.best.length === 0
                    ? "No group here can be ranked — run scoring first, or use Keep first."
                    : null,
                  bulkPlans.skipped > 0
                    ? `Skips ${bulkPlans.skipped} group${bulkPlans.skipped === 1 ? "" : "s"} with no aesthetic score — there is no best to keep there.`
                    : null,
                  bulkPlans.skippedMixed > 0
                    ? `Skips ${bulkPlans.skippedMixed} group${bulkPlans.skippedMixed === 1 ? "" : "s"} holding scores from two different aesthetic models — those scales are not comparable.`
                    : null,
                ].filter(Boolean).join(" ") || undefined}
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
              const { group, rootId, shared, anyScored, mixedModels, anyLineage } = meta;
              // Named in the tooltip rather than gestured at: "two different
              // models" tells a user nothing they can act on.
              const groupModels = [...new Set(group.map((m) => m.aesthetic_model).filter(Boolean))] as string[];
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
                        {/* Only in a mixed group, and only under the score it
                            qualifies: this is what makes the disabled *Keep
                            best* explain itself outside a tooltip. */}
                        {mixedModels && img.aesthetic_model && (
                          <span className="mono" style={{ fontSize: 9.5, color: "var(--warn)", textAlign: "center", maxWidth: 64, overflow: "hidden", textOverflow: "ellipsis" }} title={aestheticModelLabel(img.aesthetic_model)}>
                            {img.aesthetic_model}
                          </span>
                        )}
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
                    // Two refusals, one button. Mixed models is the sharper of
                    // them: ranking a LAION score against a V2.5 one and then
                    // deleting everything below the top destroys images on a
                    // number that means two different things.
                    disabled={!anyScored || mixedModels || resolveBusy}
                    title={
                      !anyScored
                        ? "No image in this group has an aesthetic score — run scoring first, or use Keep first."
                        : mixedModels
                          ? `Scored by ${groupModels.map(aestheticModelLabel).join(" and ")}, whose scales are not comparable. Re-score this group with one model, or use Keep first.`
                          : undefined
                    }
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
            // One dialog serves both paths, so the run's owner rides on the
            // pending resolution rather than on which button opened it.
            resolveMutation.mutate({
              plans: pendingResolve.plans,
              bulk: pendingResolve.kind === "bulk",
            });
            setPendingResolve(null);
          }}
        />
      )}
    </div>
  );
}
