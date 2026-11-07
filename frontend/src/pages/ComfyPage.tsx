import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  DndContext, DragOverlay, MeasuringStrategy, PointerSensor, pointerWithin, useSensor, useSensors,
  type CollisionDetection, type DragEndEvent, type DragMoveEvent, type DragStartEvent,
} from "@dnd-kit/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import { runsApi, isLiveRun, LIVE_RUN_REASON } from "../api/runs";
import { comfyApi, type ComfyPlanSummary, type ComfyRow, type LibraryPrompt, type LinkedMode } from "../api/comfy";
import ConfirmDialog from "../components/common/ConfirmDialog";
import MenuButton from "../components/common/MenuButton";
import ModalShell from "../components/common/ModalShell";
import ComfyRowsSelectionBar from "../components/comfy/ComfyRowsSelectionBar";
import BlueprintPanel from "../components/comfy/BlueprintPanel";
import WorkflowPinPanel from "../components/comfy/WorkflowPinPanel";
import ComfyRowsTable, { type RowMove } from "../components/comfy/ComfyRowsTable";
import ComfyRunBar from "../components/comfy/ComfyRunBar";
import ComfyDefaultsStrip from "../components/comfy/ComfyDefaultsStrip";
import GeneratePromptsModal from "../components/comfy/GeneratePromptsModal";
import type { GeneratePromptsJobBody } from "../components/comfy/GeneratePromptsModal";
import BulkEditRowsModal from "../components/comfy/BulkEditRowsModal";
import ComfyLibraryPanel, { type LibraryTab } from "../components/comfy/ComfyLibraryPanel";
import CopyRowsToPlanModal from "../components/comfy/CopyRowsToPlanModal";
import LinkedAskDialog from "../components/comfy/LinkedAskDialog";
import { useCopyRowsMutation } from "../components/comfy/copyRows";
import { LIBRARY_REPATH_EVENT, describeDrop, dropId, resolveDrop, type DragData, type DropAction, type DropContext, type DropData } from "../components/comfy/dnd";
import SaveToLibraryModal from "../components/comfy/SaveToLibraryModal";
import SetRowFolderModal from "../components/comfy/SetRowFolderModal";
import ComfyPlanRail from "../components/comfy/ComfyPlanRail";
import ComfyFolderTree from "../components/comfy/ComfyFolderTree";
import { inFolder, planFolderItems, type FolderFilter } from "../components/comfy/folderTree";
import FolderNameModal from "../components/comfy/FolderNameModal";
import DuplicatePlanModal from "../components/comfy/DuplicatePlanModal";
import MoveSubfolderModal from "../components/gallery/MoveSubfolderModal";
import { rowsViewKey, usePlanRowsView } from "../components/comfy/rowsView";
import { apiErrorDetail } from "../utils/apiError";
import { clearPersisted, datasetScopedKey, loadPersisted, savePersisted } from "../utils/persistentState";
import { COMFY_BLUEPRINT_SECTIONS_KEY, COMFY_PAGE_STATE_PREFIX } from "../constants/storage";
import { useStickyOffsets } from "../hooks/useStickyOffsets";
import type { SubfolderNode } from "../utils/subfolderTree";

type Section = "rows" | "workflow" | "blueprint";
type StatusFilter = "all" | ComfyRow["status"];

/** The last plan and tab, per dataset, and whether the library panel is open on which
 *  tab (M6 §8c). The plan id is checked against the plan list before use, so a deleted
 *  plan falls back to the first. */
interface PageState { planId: string | null; section: Section; libOpen: boolean; libTab: LibraryTab }
const PAGE_DEFAULTS: PageState = { planId: null, section: "rows", libOpen: false, libTab: "library" };

function loadPageState(datasetId: string | undefined): PageState {
  if (!datasetId) return { ...PAGE_DEFAULTS };
  const v = loadPersisted(datasetScopedKey(COMFY_PAGE_STATE_PREFIX, datasetId), PAGE_DEFAULTS);
  return {
    planId: typeof v.planId === "string" ? v.planId : null,
    section: v.section === "workflow" || v.section === "blueprint" ? v.section : "rows",
    libOpen: v.libOpen === true,
    libTab: v.libTab === "plans" ? "plans" : "library",
  };
}

const STATUS_CHIPS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "running", label: "Running" },
  { value: "failed", label: "Failed" },
  { value: "completed", label: "Done" },
];

const BUSY_JOB_TYPES = ["comfy_generate", "comfy_prompts", "comfy_rewrite"];
const TERMINAL_STATUSES = ["completed", "failed", "cancelled"];

/** The drag overlay (and the chip inside it) is the one element a drop must see through. */
const DRAG_OVERLAY_CLASS = "comfy-drag-overlay";
const DRAG_OVERLAY_SELECTOR = `.${DRAG_OVERLAY_CLASS}`;

const PROMPT_PIN_REASON = "Mark a pinned parameter as the prompt first";

/** The rows a drag of `rowId` moves: the whole selection, in run order, when the row is
 *  in it, else that row alone (the gallery's rule). */
function dragRowIds(rowId: string, selected: Set<string>, rows: ComfyRow[]): string[] {
  return selected.has(rowId) ? rows.filter((r) => selected.has(r.id)).map((r) => r.id) : [rowId];
}

/** Hooks can't run in loops — render one of these per active run to keep a
 *  dedicated SSE subscription per job. */
function JobSSESubscriber({ jobId }: { jobId: string }) {
  useJobSSE(jobId);
  return null;
}

export default function ComfyPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();

  const [planId, setPlanId] = useState<string | null>(() => loadPageState(datasetId).planId);
  const [section, setSection] = useState<Section>(() => loadPageState(datasetId).section);
  const [libOpen, setLibOpen] = useState(() => loadPageState(datasetId).libOpen);
  const [libTab, setLibTab] = useState<LibraryTab>(() => loadPageState(datasetId).libTab);
  // The dataset planId and section were loaded for (see the save effect below).
  const [stateDataset, setStateDataset] = useState(datasetId);
  // The Rows tab's filters: the folder picked in the tree, a text match and a status.
  // Not persisted; a plan switch resets them.
  const [folderFilter, setFolderFilter] = useState<FolderFilter>(null);
  const [textFilter, setTextFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [subfolder, setSubfolder] = useState("");
  const [setCaption, setSetCaption] = useState(true);
  // Every in-flight run: jobId → the plan it was launched against (captured at
  // mutate time). Multiple plans may run at once; each job's SSE events and
  // completion invalidation must target its own plan, not whatever plan is
  // currently selected.
  const [activeRuns, setActiveRuns] = useState<Map<string, string>>(new Map());

  // Modals
  const [confirmDeletePlan, setConfirmDeletePlan] = useState<ComfyPlanSummary | null>(null);
  const [duplicating, setDuplicating] = useState<ComfyPlanSummary | null>(null);
  const [folderName, setFolderName] = useState<{ mode: "new" | "rename"; path: string } | null>(null);
  const [movingFolder, setMovingFolder] = useState<SubfolderNode | null>(null);
  const [deletingFolder, setDeletingFolder] = useState<SubfolderNode | null>(null);
  const [confirmDeleteRows, setConfirmDeleteRows] = useState(false);
  const [showPaste, setShowPaste] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [showGenerate, setShowGenerate] = useState(false);
  // Last prompt-generation job started from this page — handed to the modal so it
  // can attach to a job it launched. The modal persists it per plan from there.
  const [promptJobId, setPromptJobId] = useState<string | null>(null);
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [copyToPlan, setCopyToPlan] = useState<{ targetId?: string; move?: boolean; rowIds?: string[] } | null>(null);
  const [showSaveToLibrary, setShowSaveToLibrary] = useState(false);
  const [showSetFolder, setShowSetFolder] = useState(false);
  // A drop that would move rows the blueprint counts, waiting on the user's choice.
  const [linkedAsk, setLinkedAsk] = useState<{
    body: Parameters<typeof comfyApi.placeRows>[1]; linked: number; total: number; folder: string;
  } | null>(null);
  const txtFilesRef = useRef<HTMLInputElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  useStickyOffsets(scrollerRef, headerRef, !!datasetId);

  /** Each .txt file is one prompt (newlines inside a file collapse to spaces);
      every non-empty file becomes a row directly — no review modal. The rows are filed
      in `folder`: the folder in view, for the button and for a drop on the table. */
  async function importPromptFiles(files: File[], folder: string) {
    const prompts: string[] = [];
    for (const f of files) {
      try {
        const text = (await f.text()).replace(/\s+/g, " ").trim();
        if (text) prompts.push(text);
      } catch {
        toast.error(`Could not read ${f.name}`);
      }
    }
    if (prompts.length === 0) {
      toast.error("No prompt text found in the selected files");
      return;
    }
    bulkAddMutation.mutate({ lines: prompts, source: "files", subfolder: folder });
  }

  // Reset local state when the dataset changes without a remount (pane mode).
  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    const saved = loadPageState(datasetId);
    setStateDataset(datasetId);
    setPlanId(saved.planId);
    setSection(saved.section);
    setLibOpen(saved.libOpen);
    setLibTab(saved.libTab);
    setFolderFilter(null);
    setTextFilter("");
    setStatusFilter("all");
    setSelected(new Set());
    setSubfolder("");
    setActiveRuns(new Map());
    setPromptJobId(null);
  }, [datasetId]);

  const { data: plans = [], isPending: plansPending } = useQuery({
    queryKey: ["comfy", "plans", datasetId],
    queryFn: () => comfyApi.listPlans(datasetId!),
    enabled: !!datasetId,
  });

  // A remembered plan is trusted only until the list first loads; after that a list
  // without it (a plan deleted here, in another pane or before a reload) falls back to
  // the first. Trusting it on every refetch flipped the page back to the deleted plan
  // each time the list refreshed. A plan made here is put into the cached list at once
  // (`addPlanToList`), so it never waits on a refetch to be known.
  const planKnown = !!planId && (plans.some((p) => p.id === planId) || plansPending);
  const effectivePlanId = (planKnown ? planId : null) ?? plans[0]?.id ?? null;

  // The shown plan changed without selectPlan (the remembered one vanished and the list
  // fell back to the first): reset what selectPlan does. Adjusting state while rendering,
  // keyed on the shown plan id, so it runs once per change.
  const [shownPlanId, setShownPlanId] = useState(effectivePlanId);
  if (shownPlanId !== effectivePlanId) {
    setShownPlanId(effectivePlanId);
    setSelected(new Set());
    setPromptJobId(null);
    setFolderFilter(null);
    setTextFilter("");
    setStatusFilter("all");
  }

  // Remember the plan and tab. Saved only once the state belongs to this dataset: in
  // the render that switches a pane's dataset, planId is still the old dataset's.
  useEffect(() => {
    if (!datasetId || stateDataset !== datasetId) return;
    savePersisted(datasetScopedKey(COMFY_PAGE_STATE_PREFIX, datasetId), { planId: effectivePlanId, section, libOpen, libTab });
  }, [datasetId, stateDataset, effectivePlanId, section, libOpen, libTab]);

  const { view: rowsView, toggle: toggleRowsView, repath: repathRowsView } = usePlanRowsView(effectivePlanId);
  const closedFolders = useMemo(() => new Set(rowsView.closedFolders), [rowsView.closedFolders]);
  const closedGroups = useMemo(() => new Set(rowsView.closedGroups), [rowsView.closedGroups]);

  const { data: plan } = useQuery({
    queryKey: ["comfy", "plan", effectivePlanId],
    queryFn: () => comfyApi.getPlan(effectivePlanId!),
    enabled: !!effectivePlanId,
  });

  const { data: rows = [] } = useQuery({
    queryKey: ["comfy", "rows", effectivePlanId],
    queryFn: () => comfyApi.listRows(effectivePlanId!),
    enabled: !!effectivePlanId,
  });

  const activeJobs = useJobStore((s) => s.activeJobs);

  // The dataset's automated Runs (the Runs page's query key, so its invalidations apply).
  // A paused Run holds no job, so the jobs above never show it; the server still refuses
  // many plan writes while one is running or paused (`_refuse_live_run`).
  const { data: datasetRuns = [] } = useQuery({
    queryKey: ["runs", datasetId],
    queryFn: () => runsApi.list(datasetId!),
    enabled: !!datasetId,
    refetchInterval: 30_000,
  });
  // Refetch when one of the dataset's jobs starts, ends or changes status (statuses only,
  // so progress ticks do not): a run stepping or ending moves a plan's dot and locks.
  const datasetJobsKey = useJobStore((s) => {
    let key = "";
    for (const j of s.activeJobs.values()) if (j.dataset_id === datasetId) key += `${j.job_id}:${j.status};`;
    return key;
  });
  useEffect(() => {
    if (datasetId && datasetJobsKey) qc.invalidateQueries({ queryKey: ["runs", datasetId] });
  }, [datasetJobsKey, datasetId, qc]);
  const { liveRunPlanIds, pausedPlanIds, stepRunPlanIds } = useMemo(() => {
    const live = new Set<string>();
    const paused = new Set<string>();
    const stepping = new Set<string>();
    for (const r of datasetRuns) {
      if (!r.plan_id || !isLiveRun(r)) continue;
      live.add(r.plan_id);
      if (r.status === "paused") paused.add(r.plan_id);
      else stepping.add(r.plan_id);
    }
    return { liveRunPlanIds: live, pausedPlanIds: paused, stepRunPlanIds: stepping };
  }, [datasetRuns]);

  useEffect(() => {
    for (const [jobId, planId] of activeRuns) {
      const progress = activeJobs.get(jobId);
      if (!progress) continue;
      if (progress.status === "running") {
        // Refresh row statuses as the run progresses (cheap list query, ~1 refetch/row),
        // and the plan list, whose status counts drive the rail's dot.
        qc.invalidateQueries({ queryKey: ["comfy", "rows", planId] });
        qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      }
      if (["completed", "failed", "cancelled"].includes(progress.status)) {
        qc.invalidateQueries({ queryKey: ["comfy", "rows", planId] });
        qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
        // A finished run moves the blueprint's kept / short / pending counts.
        qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", planId] });
        qc.invalidateQueries({ queryKey: ["images", datasetId] });
        qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
        qc.invalidateQueries({ queryKey: ["datasets"] });
        qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
        if (progress.status === "completed") toast.success("Generation run finished");
        else if (progress.status === "failed") toast.error("Generation run failed — see row errors / Logs");
        setActiveRuns((prev) => {
          const next = new Map(prev);
          next.delete(jobId);
          return next;
        });
      }
    }
  }, [activeJobs, activeRuns, datasetId, qc]);

  // ── Mutations ──────────────────────────────────────────────────────────────

  function addPlanToList(p: ComfyPlanSummary) {
    qc.setQueryData<ComfyPlanSummary[]>(["comfy", "plans", datasetId], (old) =>
      old && !old.some((x) => x.id === p.id) ? [...old, p] : old);
  }

  const createPlanMutation = useMutation({
    mutationFn: (name: string) => comfyApi.createPlan({ dataset_id: datasetId!, name }),
    onSuccess: (p) => {
      addPlanToList({ id: p.id, dataset_id: p.dataset_id, name: p.name, row_count: 0, status_counts: {} });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      selectPlan(p.id);
      setSection("workflow");
      toast.success(`Plan "${p.name}" created — paste a workflow to get started`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to create plan")),
  });

  const updatePlanMutation = useMutation({
    mutationFn: (patch: Parameters<typeof comfyApi.updatePlan>[1]) =>
      comfyApi.updatePlan(effectivePlanId!, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["comfy", "plan", effectivePlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      toast.success("Plan saved");
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to save plan")),
  });

  // Rename from the rail, which may name a plan other than the one shown.
  const renamePlanMutation = useMutation({
    mutationFn: (vars: { planId: string; name: string }) => comfyApi.updatePlan(vars.planId, { name: vars.name }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["comfy", "plan", vars.planId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      toast.success("Plan renamed");
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to rename plan")),
  });

  const deletePlanMutation = useMutation({
    mutationFn: (id: string) => comfyApi.deletePlan(id),
    onSuccess: (_data, id) => {
      // Its Blueprint sections' open state and its rows view go with it.
      clearPersisted(`${COMFY_BLUEPRINT_SECTIONS_KEY}-${id}`);
      clearPersisted(rowsViewKey(id));
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      if (id === effectivePlanId) selectPlan(null);
      setConfirmDeletePlan(null);
      toast.success("Plan deleted");
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to delete plan")),
  });

  const duplicatePlanMutation = useMutation({
    mutationFn: (vars: { planId: string; name: string; include_rows: boolean }) =>
      comfyApi.duplicatePlan(vars.planId, { name: vars.name, include_rows: vars.include_rows }),
    onSuccess: (p) => {
      addPlanToList(p);
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      setDuplicating(null);
      selectPlan(p.id);
      toast.success(`Duplicated as "${p.name}"`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to duplicate plan")),
  });

  // ── Folder tree writes. Every one is refused (409) mid-run, and a rename, move or
  // delete of a folder the blueprint counts is refused too; the toast says why.
  function invalidateFolders(pid: string) {
    qc.invalidateQueries({ queryKey: ["comfy", "rows", pid] });
    qc.invalidateQueries({ queryKey: ["comfy", "plan", pid] });
    qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
    qc.invalidateQueries({ queryKey: ["comfy", "prompts", pid] });
    qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", pid] });
    qc.invalidateQueries({ queryKey: ["comfy", "blueprint-rewrite", pid] });
  }

  // Folding a group drops its rows from the selection through the render-time prune
  // below (rows in a folded group count as hidden).
  function toggleGroup(path: string) {
    toggleRowsView("closedGroups", path);
  }

  const createFolderMutation = useMutation({
    mutationFn: (vars: { planId: string; path: string }) => comfyApi.createFolder(vars.planId, vars.path),
    onSuccess: (_data, vars) => {
      invalidateFolders(vars.planId);
      setFolderName(null);
      toast.success(`Folder "${vars.path}" created`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to create the folder")),
  });

  const moveFolderMutation = useMutation({
    mutationFn: (vars: { planId: string; from: string; to: string }) =>
      comfyApi.moveFolder(vars.planId, vars.from, vars.to),
    onSuccess: ({ updated }, vars) => {
      invalidateFolders(vars.planId);
      repathRowsView(vars.from, vars.to);
      setFolderFilter((f) => (f !== null && inFolder({ subfolder: f } as ComfyRow, vars.from) ? vars.to + f.slice(vars.from.length) : f));
      setFolderName(null);
      setMovingFolder(null);
      toast.success(`Moved "${vars.from}" to "${vars.to}" with ${updated} row${updated !== 1 ? "s" : ""}`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to move the folder")),
  });

  const deleteFolderMutation = useMutation({
    mutationFn: (vars: { planId: string; path: string }) => comfyApi.deleteFolder(vars.planId, vars.path),
    onSuccess: ({ deleted }, vars) => {
      invalidateFolders(vars.planId);
      setFolderFilter((f) => (f !== null && inFolder({ subfolder: f } as ComfyRow, vars.path) ? null : f));
      setSelected(new Set());
      setDeletingFolder(null);
      toast.success(`Deleted "${vars.path}" and ${deleted} row${deleted !== 1 ? "s" : ""}`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to delete the folder")),
  });

  const addRowMutation = useMutation({
    mutationFn: () => comfyApi.createRow(effectivePlanId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to add row")),
  });

  const bulkAddMutation = useMutation({
    // `source` names the UI that initiated the add so onSuccess only closes/clears
    // that UI — e.g. Import .txt must not wipe an in-progress paste draft.
    mutationFn: (vars: { lines: string[]; source: "paste" | "generate" | "files" | "library"; subfolder?: string }) =>
      comfyApi.bulkAddRows(effectivePlanId!, vars.lines, vars.subfolder ?? ""),
    onSuccess: (data, { source, subfolder: into }) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      // GeneratePromptsModal reads its "N in queue" count from this key, and it
      // gates a target the server re-validates — a stale count offers a run the
      // server rejects.
      qc.invalidateQueries({ queryKey: ["comfy", "prompts", effectivePlanId] });
      if (source === "paste") {
        setShowPaste(false);
        setPasteText("");
      }
      if (source === "generate") setShowGenerate(false);
      toast.success(`Added ${data.created} row${data.created !== 1 ? "s" : ""}${into ? ` to ${into}` : ""}`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to add rows")),
  });

  const deleteRowsMutation = useMutation({
    mutationFn: () => comfyApi.deleteRows(effectivePlanId!, [...selected]),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      setSelected(new Set());
      setConfirmDeleteRows(false);
      toast.success(`Deleted ${data.deleted} row${data.deleted !== 1 ? "s" : ""}`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to delete rows")),
  });

  const retryFailedMutation = useMutation({
    mutationFn: () => comfyApi.resetRows(effectivePlanId!),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      toast.success(`Reset ${data.reset} failed row${data.reset !== 1 ? "s" : ""} to pending`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to reset rows")),
  });

  const placeMutation = useMutation({
    mutationFn: (body: Parameters<typeof comfyApi.placeRows>[1]) => comfyApi.placeRows(effectivePlanId!, body),
    onSuccess: (_data, body) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
      if (body.subfolder != null) {
        qc.invalidateQueries({ queryKey: ["comfy", "plan", effectivePlanId] });
        qc.invalidateQueries({ queryKey: ["comfy", "blueprint-preview", effectivePlanId] });
      }
      setLinkedAsk(null);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to move the rows")),
  });
  const copyMutation = useCopyRowsMutation();

  // Durable "generate until N". planId travels through the variables (as in
  // runMutation) rather than being read from effectivePlanId at call time.
  // No cache invalidation here: TopBar owns it, because this job outlives both
  // the modal and this page.
  const generateJobMutation = useMutation({
    mutationFn: (vars: { planId: string; body: GeneratePromptsJobBody }) =>
      comfyApi.generatePromptsJob(vars.planId, vars.body),
    onSuccess: (data) => {
      setPromptJobId(data.job_id);
      toast.success(`Generating ${data.total} prompt${data.total !== 1 ? "s" : ""} in the background`);
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to start prompt generation")),
  });

  const runMutation = useMutation({
    // planId travels through the variables so it's captured at mutate time —
    // switching the plan select while the POST is in flight must not retag the run.
    mutationFn: (vars: { planId: string; rowIds?: string[] }) =>
      comfyApi.run({
        plan_id: vars.planId,
        row_ids: vars.rowIds,
        subfolder,
        set_caption: setCaption,
      }),
    onSuccess: (data, vars) => {
      setActiveRuns((prev) => new Map(prev).set(data.job_id, vars.planId));
      toast.success(`Generation started — ${data.total} row${data.total !== 1 ? "s" : ""}`);
      qc.invalidateQueries({ queryKey: ["comfy", "rows", vars.planId] });
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to start generation")),
  });

  // ── Derived ────────────────────────────────────────────────────────────────

  const pendingCount = rows.filter((r) => r.status === "pending").length;
  const failedCount = rows.filter((r) => r.status === "failed").length;
  // Only gate the run bar when the *currently viewed* plan has a run in flight; a
  // different plan selected mid-run must stay fully interactive.
  const viewingJobId =
    [...activeRuns.entries()].find(([, pid]) => pid === effectivePlanId)?.[0] ?? null;
  const jobProgress = viewingJobId ? activeJobs.get(viewingJobId) : undefined;
  const isRunning = runMutation.isPending || !!viewingJobId;
  const hasPromptPin = plan?.pinned_params.some((p) => p.is_prompt) ?? false;

  const promptAlias = plan?.pinned_params.find((p) => p.is_prompt)?.alias;
  const folderRows = useMemo(() => rows.filter((r) => inFolder(r, folderFilter)), [rows, folderFilter]);
  const shownRows = useMemo(() => {
    const q = textFilter.trim().toLowerCase();
    return folderRows.filter((r) => {
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (!q) return true;
      const prompt = promptAlias ? String(r.values[promptAlias] ?? "") : "";
      return prompt.toLowerCase().includes(q) || r.subfolder.toLowerCase().includes(q);
    });
  }, [folderRows, textFilter, statusFilter, promptAlias]);
  // The selection never holds a row the table does not show, whatever hid it: a filter,
  // a folder switch, a fold, a move out of view or a deleted row. Pruned while rendering
  // (React's "adjust state when inputs change"), keyed on the shown ids; it sets state
  // only when the pruned set differs, so it cannot loop.
  // The table's own rule (ComfyRowsTable's items): when every shown row is in (root) it
  // draws them flat, with no header to fold, so a stale "" in closedGroups hides nothing.
  const visibleKey = useMemo(() => {
    const flat = shownRows.every((r) => r.subfolder === "");
    return shownRows.filter((r) => flat || !closedGroups.has(r.subfolder)).map((r) => r.id).join("|");
  }, [shownRows, closedGroups]);
  const [prunedKey, setPrunedKey] = useState(visibleKey);
  if (prunedKey !== visibleKey) {
    setPrunedKey(visibleKey);
    const visible = new Set(visibleKey ? visibleKey.split("|") : []);
    const keep = new Set([...selected].filter((id) => visible.has(id)));
    if (keep.size !== selected.size) setSelected(keep);
  }
  const selectedRows = useMemo(() => rows.filter((r) => selected.has(r.id)), [rows, selected]);
  // A plan the server would refuse to delete rows from or to delete: a run this page
  // tracks, a live generate / prompts / rewrite job (GeneratePromptsModal's check), or an
  // automated Run, running or paused (the latter holds no job). The server's 409 is the backstop.
  const busyPlanIds = useMemo(() => {
    const ids = new Set([...activeRuns.values(), ...liveRunPlanIds]);
    for (const p of activeJobs.values()) {
      if (p.plan_id && BUSY_JOB_TYPES.includes(p.job_type) && !TERMINAL_STATUSES.includes(p.status)) ids.add(p.plan_id);
    }
    return ids;
  }, [activeRuns, activeJobs, liveRunPlanIds]);
  const planBusy = isRunning || (!!effectivePlanId && busyPlanIds.has(effectivePlanId));
  // A running automated Run counts as running between its child jobs too.
  const runningPlanIds = useMemo(() => new Set([...activeRuns.values(), ...stepRunPlanIds]), [activeRuns, stepRunPlanIds]);
  // The shown plan has an automated Run, running or paused: the server's 409 on the writes below.
  const liveRun = !!effectivePlanId && liveRunPlanIds.has(effectivePlanId);
  // Each folder's rows on show, in run order (the API returns rows in run order), for the
  // row menu's Move entries and their bounds. The rows on show, not all of them: under a
  // text or status filter, Move up past a hidden row would look like nothing happened.
  const folderOrder = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of shownRows) {
      const g = m.get(r.subfolder);
      if (g) g.push(r.id); else m.set(r.subfolder, [r.id]);
    }
    return m;
  }, [shownRows]);
  const rowBounds = useMemo(() => {
    const b = new Map<string, { first: boolean; last: boolean }>();
    for (const ids of folderOrder.values()) {
      ids.forEach((id, i) => b.set(id, { first: i === 0, last: i === ids.length - 1 }));
    }
    return b;
  }, [folderOrder]);
  const planFolders = useMemo(
    () => (plan ? planFolderItems(rows, plan.declared_folders ?? []).map((f) => f.path) : []),
    [rows, plan],
  );
  // Where a library prompt, an import or a copy lands when no folder is named: the
  // folder picked in the tree, or (root) when All rows is shown.
  const folderInView = folderFilter ?? "";

  // ── Drag and drop (M6 §8c; docs/dev/comfyui/library-dnd.md § Drag and drop) ──────
  // dnd-kit for every drag inside the page; desktop files are native events in the
  // table and the workflow panel. Every drop calls the mutation its button route does.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));
  const [drag, setDrag] = useState<DragData | null>(null);
  const [overAction, setOverAction] = useState<DropAction | null>(null);
  const [dropIndicator, setDropIndicator] = useState<{ rowId: string; position: "before" | "after" } | null>(null);
  // The live pointer and Shift during a drag. Shift at drop time turns a copy onto a plan
  // into a move; dnd-kit's drag end carries no modifier state. The pointer decides before
  // or after a row: dnd-kit's `delta` includes the scroll since the drag began, while a
  // droppable's rect is in viewport terms, so start + delta drifts as soon as anything
  // auto-scrolls. Seeded from the start event (Shift may already be held: macOS repeats no
  // keydown for it), then kept by capture-phase listeners, which run before dnd-kit's own
  // handlers read them.
  const [shift, setShift] = useState(false);
  const shiftRef = useRef(false);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  function noteShift(on: boolean) {
    if (shiftRef.current === on) return;
    shiftRef.current = on;
    setShift(on);
  }
  useEffect(() => {
    if (!drag) return;
    const onKey = (e: KeyboardEvent) => noteShift(e.shiftKey);
    const onMove = (e: PointerEvent) => {
      pointerRef.current = { x: e.clientX, y: e.clientY };
      noteShift(e.shiftKey);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("keyup", onKey, true);
    window.addEventListener("pointermove", onMove, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("keyup", onKey, true);
      window.removeEventListener("pointermove", onMove, true);
      shiftRef.current = false;
      setShift(false);
      pointerRef.current = null;
    };
  }, [drag]);

  // The drop context, read by the collision filter through a ref so that callback stays
  // stable across renders; refreshed after each render, before any pointer event.
  // The library is read from the panel's own query key and never fetched here
  // (enabled: false), so a no-op drop onto a prompt's own category is known.
  const { data: libraryPrompts } = useQuery({
    queryKey: ["comfy", "library"], queryFn: comfyApi.libraryList, enabled: false,
  });
  const rowFolders = useMemo(() => new Map(rows.map((r) => [r.id, r.subfolder])), [rows]);
  const promptCategories = useMemo(
    () => new Map((libraryPrompts ?? []).map((p) => [p.id, p.category])), [libraryPrompts]);
  const ctxRef = useRef({ selected, rows, planId: effectivePlanId, folderInView, rowFolders, promptCategories });
  useLayoutEffect(() => {
    ctxRef.current = { selected, rows, planId: effectivePlanId, folderInView, rowFolders, promptCategories };
  });
  const dropContext = useCallback((d: DragData, position: "before" | "after"): DropContext => {
    const c = ctxRef.current;
    return {
      rowIds: d.kind === "row" ? dragRowIds(d.rowId, c.selected, c.rows) : [],
      currentPlanId: c.planId ?? "", folderInView: c.folderInView, shift: shiftRef.current, position,
      rowFolders: c.rowFolders, promptCategories: c.promptCategories,
    };
  }, []);

  // pointerWithin, keeping only targets the drop matrix accepts, so an invalid target
  // never lights up; a row or folder under the pointer wins over the table body around it.
  // pointerWithin does not clip a rect to its scroll container, so a category scrolled out
  // of the panel, or an overscan row above the table's header, would still catch a drop:
  // a hit counts only if its node is really under the pointer (elementsFromPoint returns
  // nothing a scroll container clips away) and nothing else is painted on top of it there:
  // the first element under the pointer, past the drag overlay (and its chip), must lie
  // inside the node, so a row under the sticky header takes no drop.
  const collision: CollisionDetection = useCallback((args) => {
    const d = args.active.data.current as DragData | undefined;
    const pt = args.pointerCoordinates;
    if (!d || !pt) return [];
    const top = document.elementsFromPoint(pt.x, pt.y).find((el) => !el.closest(DRAG_OVERLAY_SELECTOR));
    const hits = pointerWithin(args).filter((h) => {
      const c = args.droppableContainers.find((x) => x.id === h.id);
      const data = c?.data.current as DropData | undefined;
      const node = c?.node.current;
      return !!data && !!node && !!top && node.contains(top) && resolveDrop(d, data, dropContext(d, "before")) !== null;
    });
    const specific = hits.filter((h) => h.id !== dropId.table);
    return specific.length ? specific : hits;
  }, [dropContext]);

  // Before or after a row: the live pointer against the row's middle. Not the dragged
  // rect (with a DragOverlay that is the small chip), and not start + delta (see above).
  function dropPosition(e: DragMoveEvent | DragEndEvent): "before" | "after" {
    const o = e.over?.rect;
    const y = pointerRef.current?.y;
    if (!o || y === undefined) return "before";
    return y < o.top + o.height / 2 ? "before" : "after";
  }

  function actionFor(e: DragMoveEvent | DragEndEvent): DropAction | null {
    const d = e.active.data.current as DragData | undefined;
    const o = e.over?.data.current as DropData | undefined;
    return d && o ? resolveDrop(d, o, dropContext(d, dropPosition(e))) : null;
  }

  const lastOverKey = useRef("");
  function onDragMove(e: DragMoveEvent) {
    const a = actionFor(e);
    const key = a ? JSON.stringify(a) : "";
    if (key === lastOverKey.current) return;
    lastOverKey.current = key;
    setOverAction(a);
    setDropIndicator(a?.type === "place" ? { rowId: a.anchor, position: a.position } : null);
  }

  function endDrag() {
    setDrag(null);
    setOverAction(null);
    setDropIndicator(null);
    lastOverKey.current = "";
  }

  async function libraryCall<T>(fn: () => Promise<T>, ok: (r: T) => string, failed: string) {
    try {
      const r = await fn();
      qc.invalidateQueries({ queryKey: ["comfy", "library"] });
      toast.success(ok(r));
    } catch (err) {
      toast.error(apiErrorDetail(err, failed));
    }
  }

  /** Move rows to a folder (and a place), asking first about rows the blueprint counts. */
  function placeWithAsk(body: Parameters<typeof comfyApi.placeRows>[1], folder: string) {
    const moving = rows.filter((r) => body.row_ids.includes(r.id) && r.subfolder !== folder);
    const linked = moving.filter((r) => r.blueprint_linked).length;
    if (linked > 0) setLinkedAsk({ body, linked, total: moving.length, folder });
    else placeMutation.mutate({ ...body, linked: "refuse" });
  }

  function runDrop(a: DropAction) {
    const n = (k: number, w: string) => `${k} ${w}${k !== 1 ? "s" : ""}`;
    switch (a.type) {
      case "set-folder":
        // At the end of the folder, as Set folder files them; a drop between rows places them.
        return placeWithAsk({ row_ids: a.ids, subfolder: a.folder }, a.folder);
      case "place":
        return placeWithAsk(
          { row_ids: a.ids, anchor_row_id: a.anchor, position: a.position, subfolder: a.folder }, a.folder);
      case "copy-rows": {
        const move = shiftRef.current;
        const linked = rows.some((r) => a.ids.includes(r.id) && r.blueprint_linked);
        if (move && linked) return setCopyToPlan({ targetId: a.planId, move: true, rowIds: a.ids });
        return copyMutation.mutate({
          sourcePlanId: effectivePlanId!, sourceDatasetId: datasetId!, targetPlanId: a.planId,
          targetDatasetId: datasetId!, targetName: a.planName, rowIds: a.ids, move,
          linked: "refuse" as LinkedMode,
        }, { onSuccess: () => { if (move) setSelected(new Set()); } });
      }
      case "save-to-library":
        return libraryCall(async () => {
          const prompts = await comfyApi.listPlanPrompts(effectivePlanId!);
          const lines = prompts.filter((p) => a.ids.includes(p.row_id)).map((p) => p.prompt.replace(/\s+/g, " ").trim());
          return comfyApi.libraryAdd(a.category, lines);
        }, (r) => `Saved ${n(r.created, "prompt")} to ${a.category}${r.skipped ? ` (${r.skipped} already there)` : ""}`,
        "Failed to save to the library");
      case "add-lib": {
        const lib = qc.getQueryData<LibraryPrompt[]>(["comfy", "library"]) ?? [];
        const byId = new Map(lib.map((p) => [p.id, p.text]));
        const lines = a.ids.map((id) => byId.get(id)?.replace(/\s+/g, " ").trim()).filter((t): t is string => !!t);
        if (lines.length) bulkAddMutation.mutate({ lines, source: "library", subfolder: a.folder });
        return;
      }
      case "move-lib":
        return libraryCall(() => comfyApi.libraryMove(a.ids, a.category),
          (r) => `Filed ${n(r.moved, "prompt")} under ${a.category}${r.merged ? `, ${r.merged} merged` : ""}`,
          "Failed to move the prompts");
      case "copy-xrows":
        return copyMutation.mutate({
          sourcePlanId: a.planId, sourceDatasetId: a.datasetId, targetPlanId: effectivePlanId!,
          targetDatasetId: datasetId!, targetName: plan?.name ?? "this plan", rowIds: a.ids, move: false,
          subfolder: a.folder,
        });
      case "move-folder":
        return moveFolderMutation.mutate({ planId: effectivePlanId!, from: a.path, to: a.newPath });
      case "move-category":
        return libraryCall(async () => {
          const r = await comfyApi.libraryMoveCategory(a.path, a.newPath);
          window.dispatchEvent(new CustomEvent(LIBRARY_REPATH_EVENT, { detail: { from: a.path, to: a.newPath } }));
          return r;
        },
          (r) => `Moved the category to ${a.newPath}${r.merged ? `, ${r.merged} merged` : ""}`,
          "Failed to move the category");
    }
  }

  function onDragEnd(e: DragEndEvent) {
    const a = actionFor(e);
    endDrag();
    if (a) runDrop(a);
  }

  const dragCount = !drag ? 0
    : drag.kind === "row" ? dragRowIds(drag.rowId, selected, rows).length
    : drag.kind === "lib" || drag.kind === "xrows" ? drag.ids.length : 1;
  const dragNoun = !drag ? ""
    : drag.kind === "folder" ? `folder ${drag.path}` : drag.kind === "category" ? `category ${drag.path}`
    : `${dragCount} ${drag.kind === "lib" ? "prompt" : "row"}${dragCount !== 1 ? "s" : ""}`;
  const overLabel = overAction
    ? describeDrop(overAction.type === "copy-rows" ? { ...overAction, move: shift } : overAction)
    : null;

  /** The row menu's moves, within the row's folder, through `rows/place`. */
  function moveRow(row: ComfyRow, where: RowMove) {
    const ids = folderOrder.get(row.subfolder) ?? [];
    const i = ids.indexOf(row.id);
    const anchor = where === "top" ? ids[0] : where === "up" ? ids[i - 1]
      : where === "down" ? ids[i + 1] : ids[ids.length - 1];
    if (!anchor || anchor === row.id) return;
    placeMutation.mutate({
      row_ids: [row.id], anchor_row_id: anchor,
      position: where === "top" || where === "up" ? "before" : "after",
    });
  }

  /** Switch plans: a new plan starts with no selection, no filter and no attached job. */
  function selectPlan(id: string | null) {
    setPlanId(id);
    setSelected(new Set());
    setPromptJobId(null);
    setFolderFilter(null);
    setTextFilter("");
    setStatusFilter("all");
  }

  // A selection the filters hide would still be acted on by Set folder and Delete, so
  // narrowing the view clears it.
  function changeFilter(apply: () => void) {
    apply();
    setSelected(new Set());
  }

  function toggleRow(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleAll(ids: string[], select: boolean) {
    setSelected(select ? new Set(ids) : new Set());
  }

  if (!datasetId) {
    return <div style={{ padding: 24, color: "var(--fg-mute)" }}>No dataset selected.</div>;
  }

  return (
    // Its own bounded scroller in both shells: outside pane mode `main` would scroll
    // instead, with no fixed height for the sticky side column to fit. The scroll
    // padding keeps scrollIntoView (and Playwright's) clear of the sticky header. No top
    // padding on the scroller: sticky `top: 0` counts from inside it, so the header would
    // stop 24px down with content showing above it. The title carries that space instead.
    <div ref={scrollerRef} style={{
      height: "100%", minHeight: 0, overflowY: "auto", padding: "0 28px 24px",
      scrollPaddingTop: "calc(var(--comfy-head-h, 0px) + 8px)",
    }}>
      {[...activeRuns.keys()].map((jobId) => (
        <JobSSESubscriber key={jobId} jobId={jobId} />
      ))}
      <div className="page-h" style={{ paddingTop: 24 }}>
        <div>
          <h1>ComfyUI generation</h1>
          <p>Manage prompt queues, run them on your ComfyUI server, and import the results into this dataset.</p>
        </div>
      </div>

      {/* The sticky header: the plan's name with the tabs, and the run bar on every tab.
          Plans are picked, made, renamed and deleted in the Rows tab's rail. */}
      <div className="comfy-head" ref={headerRef} data-testid="comfy-head">
      {plan && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span data-testid="comfy-plan-name" style={{ fontSize: 13, fontWeight: 600 }}>{plan.name}</span>
          <div className="tabs" style={{ marginBottom: 0 }}>
            <button className={`tab${section === "rows" ? " active" : ""}`} onClick={() => setSection("rows")}>Rows</button>
            <button className={`tab${section === "workflow" ? " active" : ""}`} onClick={() => setSection("workflow")}>Workflow &amp; Pins</button>
            <button className={`tab${section === "blueprint" ? " active" : ""}`} onClick={() => setSection("blueprint")}>Blueprint</button>
          </div>
        </div>
      )}
      {plan && (
        <ComfyRunBar
          datasetId={datasetId}
          pendingCount={pendingCount}
          totalCount={rows.length}
          subfolder={subfolder}
          onSubfolderChange={setSubfolder}
          setCaption={setCaption}
          onSetCaptionChange={setSetCaption}
          onRunPending={() => runMutation.mutate({ planId: effectivePlanId! })}
          onRunAll={() => runMutation.mutate({ planId: effectivePlanId!, rowIds: rows.map((r) => r.id) })}
          isRunning={isRunning}
          jobProgress={jobProgress}
        />
      )}
      </div>


      {plan && section === "workflow" && (
        <WorkflowPinPanel
          key={plan.id + plan.updated_at}
          plan={plan}
          saving={updatePlanMutation.isPending}
          onSave={(patch) => updatePlanMutation.mutate(patch)}
        />
      )}

      {plan && section === "blueprint" && (
        <BlueprintPanel
          key={plan.id}
          planId={plan.id}
          datasetId={datasetId}
          hasPromptPin={hasPromptPin}
          running={isRunning}
          liveRun={liveRun}
          subfolder={subfolder}
          setCaption={setCaption}
          onRunStarted={(jobId, pid) => setActiveRuns((prev) => new Map(prev).set(jobId, pid))}
        />
      )}

      {(section === "rows" || plans.length === 0) && (
        <DndContext
          sensors={sensors}
          collisionDetection={collision}
          // Rows scroll inside the virtualizer, so rects measured at drag start go stale.
          measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
          onDragStart={(e: DragStartEvent) => {
            const start = e.activatorEvent as PointerEvent | null;
            if (start && typeof start.clientY === "number") pointerRef.current = { x: start.clientX, y: start.clientY };
            noteShift(!!start?.shiftKey);
            setDrag((e.active.data.current as DragData | undefined) ?? null);
          }}
          onDragMove={onDragMove}
          onDragEnd={onDragEnd}
          onDragCancel={endDrag}
        >
        <div className="comfy-rows-wrap">
        <div className={`comfy-rows-grid${libOpen && plan && section === "rows" ? " lib-open" : ""}`}>
          <aside className="comfy-rail">
            <ComfyPlanRail
              plans={plans}
              activePlanId={effectivePlanId}
              runningPlanIds={runningPlanIds}
              pausedPlanIds={pausedPlanIds}
              liveRunPlanIds={liveRunPlanIds}
              busyPlanIds={busyPlanIds}
              onSelect={selectPlan}
              onCreate={(name) => createPlanMutation.mutate(name)}
              creating={createPlanMutation.isPending}
              onRename={(id, name) => renamePlanMutation.mutate({ planId: id, name })}
              renaming={renamePlanMutation.isPending}
              onDuplicate={setDuplicating}
              onDelete={setConfirmDeletePlan}
            />
            {plan && (
              <ComfyFolderTree
                rows={rows}
                declared={plan.declared_folders ?? []}
                selected={folderFilter}
                onSelect={(f) => changeFilter(() => setFolderFilter(f))}
                closed={closedFolders}
                onToggle={(path) => toggleRowsView("closedFolders", path)}
                busy={planBusy}
                onNew={(parent) => setFolderName({ mode: "new", path: parent })}
                onRename={(node) => setFolderName({ mode: "rename", path: node.path })}
                onMove={setMovingFolder}
                onDelete={setDeletingFolder}
              />
            )}
          </aside>
          <div style={{ minWidth: 0 }}>
          {plans.length === 0 && !plansPending && (
            <div className="panel">
              <div className="panel-b">
                <p style={{ fontSize: 13, color: "var(--fg-mute)", margin: 0 }}>
                  No plans yet. A plan pairs a ComfyUI workflow (API-format export) with a queue of prompt/parameter
                  rows. Create one to get started — and set your ComfyUI server URL in Settings → ComfyUI if you
                  haven't yet.
                </p>
              </div>
            </div>
          )}
          {plan && section === "rows" && (
          <>
          <ComfyDefaultsStrip
            plan={plan}
            disabled={isRunning || updatePlanMutation.isPending}
            onSavePins={(pins) => updatePlanMutation.mutate({ pinned_params: pins })}
          />

          {/* Rows toolbar (M6 §8d): the filters, then Add ▾ and the Library toggle. Outside the
              rows table on purpose, whose inputs the e2e counts as row cells. */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
            <input
              className="input" aria-label="Filter prompts" placeholder="Filter prompts and folders…"
              style={{ width: 240, height: 28, fontSize: 12 }}
              value={textFilter}
              onChange={(e) => { const v = e.target.value; changeFilter(() => setTextFilter(v)); }}
            />
            <div role="group" aria-label="Status" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {STATUS_CHIPS.map((c) => {
                const n = c.value === "all" ? folderRows.length : folderRows.filter((r) => r.status === c.value).length;
                const on = statusFilter === c.value;
                return (
                  <button
                    key={c.value}
                    className={`btn sm ${on ? "primary" : "ghost"}`}
                    aria-pressed={on}
                    onClick={() => changeFilter(() => setStatusFilter(c.value))}
                  >
                    {c.label} <span className="mono" style={{ opacity: 0.7 }}>{n}</span>
                  </button>
                );
              })}
            </div>
            {folderFilter !== null && (
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>
                in <span className="mono">{folderFilter || "(root)"}</span>
              </span>
            )}
            <div style={{ flex: 1 }} />
            {failedCount > 0 && (
              <button className="btn ghost sm" onClick={() => retryFailedMutation.mutate()}>
                Reset failed ({failedCount})
              </button>
            )}
            <MenuButton
              label="Add"
              align="right"
              testId="comfy-add-menu"
              actions={[
                {
                  label: "Add row",
                  onClick: () => addRowMutation.mutate(),
                  disabled: plan.pinned_params.length === 0 || liveRun,
                  title: liveRun ? LIVE_RUN_REASON : plan.pinned_params.length === 0 ? "Pin a workflow parameter first" : "Add one empty row",
                },
                {
                  label: "Paste prompts…",
                  onClick: () => setShowPaste(true),
                  disabled: !hasPromptPin || liveRun,
                  title: liveRun ? LIVE_RUN_REASON : hasPromptPin ? "Add one row per line of prompt text" : PROMPT_PIN_REASON,
                },
                {
                  label: "Import .txt…",
                  // Synchronous inside the click, so the file picker keeps its user activation.
                  onClick: () => txtFilesRef.current?.click(),
                  disabled: !hasPromptPin || bulkAddMutation.isPending || liveRun,
                  title: liveRun ? LIVE_RUN_REASON : hasPromptPin ? "Add one row per selected .txt file, in the folder in view" : PROMPT_PIN_REASON,
                },
                {
                  label: "Generate prompts…",
                  onClick: () => setShowGenerate(true),
                  disabled: !hasPromptPin,
                  title: hasPromptPin ? "Generate prompts with an LLM provider" : PROMPT_PIN_REASON,
                },
                {
                  label: "Edit prompts…",
                  onClick: () => setShowBulkEdit(true),
                  disabled: !hasPromptPin || rows.length === 0,
                  title: !hasPromptPin
                    ? PROMPT_PIN_REASON
                    : rows.length === 0
                      ? "There are no rows to edit yet"
                      : "Find & replace / prepend / append / remove across row prompts",
                },
              ]}
            />
            <input
              ref={txtFilesRef} type="file" accept=".txt,text/plain" multiple style={{ display: "none" }}
              onChange={(e) => {
                if (e.target.files?.length) importPromptFiles(Array.from(e.target.files), folderInView);
                e.target.value = "";
              }}
            />
            <button className={`btn sm ${libOpen ? "primary" : "ghost"}`} aria-pressed={libOpen}
              onClick={() => setLibOpen((v) => !v)}
              title="Show the prompt library and other plans' rows beside the table">
              Library
            </button>
          </div>

          <ComfyRowsSelectionBar
            count={selected.size}
            hasPromptPin={hasPromptPin}
            isRunning={isRunning}
            planBusy={planBusy}
            liveRunReason={liveRun ? LIVE_RUN_REASON : undefined}
            onSetFolder={() => setShowSetFolder(true)}
            onCopyToPlan={() => setCopyToPlan({})}
            onSaveToLibrary={() => setShowSaveToLibrary(true)}
            onRunSelected={() => runMutation.mutate({ planId: effectivePlanId!, rowIds: [...selected] })}
            onDelete={() => setConfirmDeleteRows(true)}
            onClear={() => setSelected(new Set())}
          />

          <ComfyRowsTable
            plan={plan}
            rows={shownRows}
            totalRows={rows.length}
            selected={selected}
            onToggle={toggleRow}
            onToggleAll={toggleAll}
            runningJob={isRunning}
            closedGroups={closedGroups}
            onToggleGroup={toggleGroup}
            rowBounds={rowBounds}
            onMoveRow={moveRow}
            draggingRowId={drag?.kind === "row" ? drag.rowId : null}
            dropIndicator={dropIndicator}
            onDropFiles={(files) => importPromptFiles(files, folderInView)}
          />
          </>
          )}
          </div>
          {libOpen && plan && section === "rows" && (
            <ComfyLibraryPanel
              key={datasetId}
              datasetId={datasetId}
              plan={plan}
              folders={planFolders}
              folderInView={folderInView}
              tab={libTab}
              onTabChange={setLibTab}
              onClose={() => setLibOpen(false)}
              liveRunPlanIds={liveRunPlanIds}
            />
          )}
        </div>
        </div>
        <DragOverlay dropAnimation={null} zIndex={60} className={DRAG_OVERLAY_CLASS}>
          {drag && (
            <div style={{
              display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 10px", fontSize: 12,
              borderRadius: 999, background: "var(--surface-3)", border: "1px solid var(--accent)",
              boxShadow: "0 8px 24px rgba(0,0,0,.35)", whiteSpace: "nowrap", pointerEvents: "none",
            }}>
              <span style={{ fontWeight: 600 }}>{dragNoun}</span>
              {overLabel && <span style={{ color: "var(--fg-mute)" }}>→ {overLabel}</span>}
            </div>
          )}
        </DragOverlay>
        </DndContext>
      )}

      {/* Paste prompts. The draft lives in this page, so Escape or Cancel keeps it for
          the next open, and only a paste's own add clears it (bulkAddMutation's source). */}
      {showPaste && (
        <ModalShell label="Paste prompts" onClose={() => setShowPaste(false)} closeOnBackdrop
          style={{ width: 560, maxWidth: "92vw" }}>
          <h3>Paste prompts</h3>
          <p>
            One prompt per line — each becomes a row with that prompt; other parameters use the template
            values.
          </p>
          <textarea
            className="input" autoFocus value={pasteText} onChange={(e) => setPasteText(e.target.value)}
            style={{ width: "100%", minHeight: 200, fontSize: 12, resize: "vertical" }}
            placeholder={"a cat sitting on a windowsill\na dog running on a beach\n…"}
          />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
            <button className="btn ghost" onClick={() => setShowPaste(false)}>Cancel</button>
            <button
              className="btn primary"
              disabled={!pasteText.trim() || bulkAddMutation.isPending}
              onClick={() => bulkAddMutation.mutate({ lines: pasteText.split("\n"), source: "paste" })}
            >
              Add {pasteText.split("\n").filter((l) => l.trim()).length} rows
            </button>
          </div>
        </ModalShell>
      )}

      {showGenerate && plan && (
        <GeneratePromptsModal
          // Per-plan persisted settings and the attached job id are read in state
          // initializers, so the modal must remount when the plan changes.
          key={plan.id}
          planId={plan.id}
          adding={bulkAddMutation.isPending}
          onAdd={(prompts) => bulkAddMutation.mutate({ lines: prompts, source: "generate" })}
          onStartJob={(body) => generateJobMutation.mutate({ planId: plan.id, body })}
          startingJob={generateJobMutation.isPending}
          startedJobId={promptJobId}
          onClose={() => setShowGenerate(false)}
        />
      )}

      {showBulkEdit && plan && (
        <BulkEditRowsModal
          plan={plan}
          rowCount={rows.length}
          selectedIds={[...selected]}
          onClose={() => setShowBulkEdit(false)}
        />
      )}

      {linkedAsk && (
        <LinkedAskDialog
          linked={linkedAsk.linked}
          total={linkedAsk.total}
          folder={linkedAsk.folder}
          busy={placeMutation.isPending}
          onChoose={(linked) => placeMutation.mutate({ ...linkedAsk.body, linked })}
          onCancel={() => setLinkedAsk(null)}
        />
      )}

      {copyToPlan && plan && (
        <CopyRowsToPlanModal
          sourcePlanId={plan.id}
          datasetId={datasetId}
          plans={plans}
          rows={copyToPlan.rowIds ? rows.filter((r) => copyToPlan.rowIds!.includes(r.id)) : selectedRows}
          initialTargetId={copyToPlan.targetId}
          initialMove={copyToPlan.move}
          onMoved={() => setSelected(new Set())}
          liveRunPlanIds={liveRunPlanIds}
          onClose={() => setCopyToPlan(null)}
        />
      )}

      {showSaveToLibrary && plan && (
        <SaveToLibraryModal
          planId={plan.id}
          rowIds={[...selected]}
          onClose={() => setShowSaveToLibrary(false)}
        />
      )}

      {showSetFolder && plan && (
        <SetRowFolderModal
          planId={plan.id}
          datasetId={plan.dataset_id}
          rows={selectedRows}
          onClose={() => setShowSetFolder(false)}
        />
      )}

      {confirmDeletePlan && (
        <ConfirmDialog
          title="Delete plan"
          message={`Delete plan "${confirmDeletePlan.name}" and its ${confirmDeletePlan.row_count} row${confirmDeletePlan.row_count !== 1 ? "s" : ""}? Generated images already imported into the dataset are kept.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deletePlanMutation.mutate(confirmDeletePlan.id)}
          onCancel={() => setConfirmDeletePlan(null)}
        />
      )}

      {duplicating && (
        <DuplicatePlanModal
          plan={duplicating}
          takenNames={plans.map((p) => p.name)}
          isPending={duplicatePlanMutation.isPending}
          onConfirm={(body) => duplicatePlanMutation.mutate({ planId: duplicating.id, ...body })}
          onClose={() => setDuplicating(null)}
        />
      )}

      {folderName && plan && (
        <FolderNameModal
          mode={folderName.mode}
          path={folderName.path}
          isPending={createFolderMutation.isPending || moveFolderMutation.isPending}
          onConfirm={(newPath) => {
            if (folderName.mode === "new") createFolderMutation.mutate({ planId: plan.id, path: newPath });
            else moveFolderMutation.mutate({ planId: plan.id, from: folderName.path, to: newPath });
          }}
          onClose={() => setFolderName(null)}
        />
      )}

      {movingFolder && plan && (
        <MoveSubfolderModal
          node={movingFolder}
          subfolders={planFolderItems(rows, plan.declared_folders ?? [])}
          isPending={moveFolderMutation.isPending}
          onConfirm={(newPath) => moveFolderMutation.mutate({ planId: plan.id, from: movingFolder.path, to: newPath })}
          onClose={() => setMovingFolder(null)}
        />
      )}

      {deletingFolder && plan && (() => {
        const n = rows.filter((r) => inFolder(r, deletingFolder.path)).length;
        return (
          <ConfirmDialog
            title="Delete folder"
            message={`Delete "${deletingFolder.path}", its subfolders and ${n} row${n !== 1 ? "s" : ""}? Images already made stay in the gallery.`}
            confirmLabel="Delete"
            danger
            onConfirm={() => deleteFolderMutation.mutate({ planId: plan.id, path: deletingFolder.path })}
            onCancel={() => setDeletingFolder(null)}
          />
        );
      })()}

      {confirmDeleteRows && (
        <ConfirmDialog
          title="Delete rows"
          message={`Delete ${selected.size} selected row${selected.size !== 1 ? "s" : ""}? Generated images already imported are kept.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteRowsMutation.mutate()}
          onCancel={() => setConfirmDeleteRows(false)}
        />
      )}
    </div>
  );
}
