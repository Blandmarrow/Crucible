import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import { comfyApi, type ComfyPlanSummary, type ComfyRow } from "../api/comfy";
import ConfirmDialog from "../components/common/ConfirmDialog";
import BlueprintPanel from "../components/comfy/BlueprintPanel";
import WorkflowPinPanel from "../components/comfy/WorkflowPinPanel";
import ComfyRowsTable from "../components/comfy/ComfyRowsTable";
import ComfyRunBar from "../components/comfy/ComfyRunBar";
import ComfyDefaultsStrip from "../components/comfy/ComfyDefaultsStrip";
import GeneratePromptsModal from "../components/comfy/GeneratePromptsModal";
import type { GeneratePromptsJobBody } from "../components/comfy/GeneratePromptsModal";
import BulkEditRowsModal from "../components/comfy/BulkEditRowsModal";
import PromptLibraryModal from "../components/comfy/PromptLibraryModal";
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

/** The last plan and tab, per dataset. The plan id is checked against the plan list
 *  before use, so a deleted plan falls back to the first. */
interface PageState { planId: string | null; section: Section }
const PAGE_DEFAULTS: PageState = { planId: null, section: "rows" };

function loadPageState(datasetId: string | undefined): PageState {
  if (!datasetId) return { ...PAGE_DEFAULTS };
  const v = loadPersisted(datasetScopedKey(COMFY_PAGE_STATE_PREFIX, datasetId), PAGE_DEFAULTS);
  return {
    planId: typeof v.planId === "string" ? v.planId : null,
    section: v.section === "workflow" || v.section === "blueprint" ? v.section : "rows",
  };
}

const STATUS_CHIPS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "running", label: "Running" },
  { value: "failed", label: "Failed" },
  { value: "completed", label: "Done" },
];

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
  const [showLibrary, setShowLibrary] = useState(false);
  const [showSaveToLibrary, setShowSaveToLibrary] = useState(false);
  const [showSetFolder, setShowSetFolder] = useState(false);
  const txtFilesRef = useRef<HTMLInputElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  useStickyOffsets(scrollerRef, headerRef, !!datasetId);

  /** Each .txt file is one prompt (newlines inside a file collapse to spaces);
      every non-empty file becomes a row directly — no review modal. */
  async function importPromptFiles(files: FileList) {
    const prompts: string[] = [];
    for (const f of Array.from(files)) {
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
    bulkAddMutation.mutate({ lines: prompts, source: "files" });
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
    setFolderFilter(null);
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

  // Remember the plan and tab. Saved only once the state belongs to this dataset: in
  // the render that switches a pane's dataset, planId is still the old dataset's.
  useEffect(() => {
    if (!datasetId || stateDataset !== datasetId) return;
    savePersisted(datasetScopedKey(COMFY_PAGE_STATE_PREFIX, datasetId), { planId: effectivePlanId, section });
  }, [datasetId, stateDataset, effectivePlanId, section]);

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
    onError: () => toast.error("Failed to delete plan"),
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

  // Folding a group drops its rows from the selection: Delete and Run selected must
  // never act on rows the user cannot see (the same rule as a filter change).
  function toggleGroup(path: string) {
    if (!closedGroups.has(path)) {
      setSelected((prev) => {
        const next = new Set(prev);
        for (const r of rows) if (r.subfolder === path) next.delete(r.id);
        return next;
      });
    }
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
    onSuccess: () => qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] }),
    onError: () => toast.error("Failed to add row"),
  });

  const bulkAddMutation = useMutation({
    // `source` names the UI that initiated the add so onSuccess only closes/clears
    // that UI — e.g. Import .txt must not wipe an in-progress paste draft.
    mutationFn: (vars: { lines: string[]; source: "paste" | "generate" | "files" }) =>
      comfyApi.bulkAddRows(effectivePlanId!, vars.lines),
    onSuccess: (data, { source }) => {
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
      toast.success(`Added ${data.created} row${data.created !== 1 ? "s" : ""}`);
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
    onError: () => toast.error("Failed to delete rows"),
  });

  const retryFailedMutation = useMutation({
    mutationFn: () => comfyApi.resetRows(effectivePlanId!),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
      toast.success(`Reset ${data.reset} failed row${data.reset !== 1 ? "s" : ""} to pending`);
    },
    onError: () => toast.error("Failed to reset rows"),
  });

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
  const selectedRows = useMemo(() => rows.filter((r) => selected.has(r.id)), [rows, selected]);
  const runningPlanIds = useMemo(() => new Set(activeRuns.values()), [activeRuns]);

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
          selectedCount={selected.size}
          totalCount={rows.length}
          subfolder={subfolder}
          onSubfolderChange={setSubfolder}
          setCaption={setCaption}
          onSetCaptionChange={setSetCaption}
          onRunPending={() => runMutation.mutate({ planId: effectivePlanId! })}
          onRunSelected={() => runMutation.mutate({ planId: effectivePlanId!, rowIds: [...selected] })}
          showRunSelected={section === "rows"}
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
          subfolder={subfolder}
          setCaption={setCaption}
          onRunStarted={(jobId, pid) => setActiveRuns((prev) => new Map(prev).set(jobId, pid))}
        />
      )}

      {(section === "rows" || plans.length === 0) && (
        <div className="comfy-rows-wrap">
        <div className="comfy-rows-grid">
          <aside className="comfy-rail">
            <ComfyPlanRail
              plans={plans}
              activePlanId={effectivePlanId}
              runningPlanIds={runningPlanIds}
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
                busy={isRunning}
                onNew={(parent) => setFolderName({ mode: "new", path: parent })}
                onRename={(node) => setFolderName({ mode: "rename", path: node.path })}
                onMove={setMovingFolder}
                onDelete={setDeletingFolder}
              />
            )}
          </aside>
          <div style={{ minWidth: 0 }}>
          {plans.length === 0 && (
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

          {/* Rows toolbar. It wraps: beside the rail it no longer fits one line (§8d trims it). */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
            <button className="btn ghost sm" onClick={() => addRowMutation.mutate()} disabled={plan.pinned_params.length === 0}>+ Add row</button>
            <button className="btn ghost sm" onClick={() => setShowPaste(true)}
              disabled={!hasPromptPin}
              title={hasPromptPin ? "Add one row per line of prompt text" : "Mark a pinned parameter as the prompt first"}>
              Paste prompts…
            </button>
            <button className="btn ghost sm" onClick={() => txtFilesRef.current?.click()}
              disabled={!hasPromptPin || bulkAddMutation.isPending}
              title={hasPromptPin ? "Add one row per selected .txt file" : "Mark a pinned parameter as the prompt first"}>
              Import .txt
            </button>
            <input
              ref={txtFilesRef} type="file" accept=".txt,text/plain" multiple style={{ display: "none" }}
              onChange={(e) => { if (e.target.files?.length) importPromptFiles(e.target.files); e.target.value = ""; }}
            />
            <button className="btn ghost sm" onClick={() => setShowGenerate(true)}
              disabled={!hasPromptPin}
              title={hasPromptPin ? "Generate prompts with an LLM provider" : "Mark a pinned parameter as the prompt first"}>
              Generate prompts…
            </button>
            <button className="btn ghost sm" onClick={() => setShowBulkEdit(true)}
              disabled={!hasPromptPin || rows.length === 0}
              title={hasPromptPin ? "Find & replace / prepend / append / remove across row prompts" : "Mark a pinned parameter as the prompt first"}>
              Edit prompts…
            </button>
            <button className="btn ghost sm" onClick={() => setShowLibrary(true)}
              disabled={!hasPromptPin}
              title={hasPromptPin ? "Browse the prompt library or reuse prompts from another plan" : "Mark a pinned parameter as the prompt first"}>
              Library…
            </button>
            <div style={{ flex: 1 }} />
            <button className="btn ghost sm" onClick={() => setShowSaveToLibrary(true)}
              disabled={!hasPromptPin || selected.size === 0}
              title={hasPromptPin ? "Save the selected rows' prompts to the prompt library" : "Mark a pinned parameter as the prompt first"}>
              Save to library ({selected.size})
            </button>
            <button className="btn ghost sm" onClick={() => setShowSetFolder(true)}
              disabled={selected.size === 0 || isRunning}
              title="Set the destination folder on the selected rows">
              Set folder ({selected.size})
            </button>
            {failedCount > 0 && (
              <button className="btn ghost sm" onClick={() => retryFailedMutation.mutate()}>
                Reset failed ({failedCount})
              </button>
            )}
            <button className="btn ghost sm" style={{ color: "var(--bad)" }} disabled={selected.size === 0}
              onClick={() => setConfirmDeleteRows(true)}>
              Delete selected ({selected.size})
            </button>
          </div>

          {/* Filters: outside the rows table, whose inputs the e2e counts as row cells. */}
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
          </div>

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
          />
          </>
          )}
          </div>
        </div>
        </div>
      )}

      {/* Paste prompts modal */}
      {showPaste && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setShowPaste(false)}
        >
          <div
            className="panel" style={{ width: 560, maxWidth: "92vw" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="panel-h">
              <h3>Paste prompts</h3>
            </div>
            <div className="panel-b">
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 8px" }}>
                One prompt per line — each becomes a row with that prompt; other parameters use the template
                values.
              </p>
              <textarea
                className="input" autoFocus value={pasteText} onChange={(e) => setPasteText(e.target.value)}
                style={{ width: "100%", minHeight: 200, fontSize: 12, resize: "vertical" }}
                placeholder={"a cat sitting on a windowsill\na dog running on a beach\n…"}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
                <button className="btn ghost" onClick={() => setShowPaste(false)}>Cancel</button>
                <button
                  className="btn primary"
                  disabled={!pasteText.trim() || bulkAddMutation.isPending}
                  onClick={() => bulkAddMutation.mutate({ lines: pasteText.split("\n"), source: "paste" })}
                >
                  Add {pasteText.split("\n").filter((l) => l.trim()).length} rows
                </button>
              </div>
            </div>
          </div>
        </div>
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

      {showLibrary && plan && (
        <PromptLibraryModal
          targetPlanId={plan.id}
          targetDatasetId={datasetId}
          onImported={() => qc.invalidateQueries({ queryKey: ["comfy", "rows", plan.id] })}
          onClose={() => setShowLibrary(false)}
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
