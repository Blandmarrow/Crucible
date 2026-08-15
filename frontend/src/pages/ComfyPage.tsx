import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import { comfyApi } from "../api/comfy";
import ConfirmDialog from "../components/common/ConfirmDialog";
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
import { apiErrorDetail } from "../utils/apiError";

/** Hooks can't run in loops — render one of these per active run to keep a
 *  dedicated SSE subscription per job. */
function JobSSESubscriber({ jobId }: { jobId: string }) {
  useJobSSE(jobId);
  return null;
}

export default function ComfyPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();

  const [planId, setPlanId] = useState<string | null>(null);
  const [section, setSection] = useState<"rows" | "workflow">("rows");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [subfolder, setSubfolder] = useState("");
  const [setCaption, setSetCaption] = useState(true);
  // Every in-flight run: jobId → the plan it was launched against (captured at
  // mutate time). Multiple plans may run at once; each job's SSE events and
  // completion invalidation must target its own plan, not whatever plan is
  // currently selected.
  const [activeRuns, setActiveRuns] = useState<Map<string, string>>(new Map());

  // Modals / inline forms
  const [showCreate, setShowCreate] = useState(false);
  const [newPlanName, setNewPlanName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDeletePlan, setConfirmDeletePlan] = useState(false);
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
    setPlanId(null);
    setSelected(new Set());
    setSubfolder("");
    setActiveRuns(new Map());
    setPromptJobId(null);
  }, [datasetId]);

  const { data: plans = [] } = useQuery({
    queryKey: ["comfy", "plans", datasetId],
    queryFn: () => comfyApi.listPlans(datasetId!),
    enabled: !!datasetId,
  });

  const effectivePlanId = planId ?? plans[0]?.id ?? null;

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
        // Refresh row statuses as the run progresses (cheap list query, ~1 refetch/row).
        qc.invalidateQueries({ queryKey: ["comfy", "rows", planId] });
      }
      if (["completed", "failed", "cancelled"].includes(progress.status)) {
        qc.invalidateQueries({ queryKey: ["comfy", "rows", planId] });
        qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
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

  const createPlanMutation = useMutation({
    mutationFn: (name: string) => comfyApi.createPlan({ dataset_id: datasetId!, name }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      setPlanId(p.id);
      setShowCreate(false);
      setNewPlanName("");
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
      setRenaming(false);
      toast.success("Plan saved");
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to save plan")),
  });

  const deletePlanMutation = useMutation({
    mutationFn: () => comfyApi.deletePlan(effectivePlanId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      setPlanId(null);
      setConfirmDeletePlan(false);
      toast.success("Plan deleted");
    },
    onError: () => toast.error("Failed to delete plan"),
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
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      {[...activeRuns.keys()].map((jobId) => (
        <JobSSESubscriber key={jobId} jobId={jobId} />
      ))}
      <div className="page-h">
        <div>
          <h1>ComfyUI generation</h1>
          <p>Manage prompt queues, run them on your ComfyUI server, and import the results into this dataset.</p>
        </div>
      </div>

      {/* Plan bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        {plans.length > 0 && (
          <select
            className="select"
            value={effectivePlanId ?? ""}
            onChange={(e) => { setPlanId(e.target.value); setSelected(new Set()); setPromptJobId(null); }}
            style={{ minWidth: 220 }}
          >
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.row_count} row{p.row_count !== 1 ? "s" : ""})
              </option>
            ))}
          </select>
        )}
        {showCreate ? (
          <>
            <input
              className="input" autoFocus placeholder="Plan name" value={newPlanName}
              onChange={(e) => setNewPlanName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newPlanName.trim()) createPlanMutation.mutate(newPlanName.trim());
                if (e.key === "Escape") { setShowCreate(false); setNewPlanName(""); }
              }}
              style={{ width: 200 }}
            />
            <button className="btn primary sm" disabled={!newPlanName.trim() || createPlanMutation.isPending}
              onClick={() => createPlanMutation.mutate(newPlanName.trim())}>Create</button>
            <button className="btn ghost sm" onClick={() => { setShowCreate(false); setNewPlanName(""); }}>Cancel</button>
          </>
        ) : (
          <button className="btn ghost sm" onClick={() => setShowCreate(true)}>+ New plan</button>
        )}
        {plan && !showCreate && (
          renaming ? (
            <>
              <input
                className="input" autoFocus value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && renameValue.trim()) updatePlanMutation.mutate({ name: renameValue.trim() });
                  if (e.key === "Escape") setRenaming(false);
                }}
                style={{ width: 200 }}
              />
              <button className="btn primary sm" disabled={!renameValue.trim() || updatePlanMutation.isPending}
                onClick={() => updatePlanMutation.mutate({ name: renameValue.trim() })}>Save</button>
              <button className="btn ghost sm" onClick={() => setRenaming(false)}>Cancel</button>
            </>
          ) : (
            <>
              <button className="btn ghost sm" onClick={() => { setRenameValue(plan.name); setRenaming(true); }}>Rename</button>
              <button className="btn ghost sm" style={{ color: "var(--bad)" }} onClick={() => setConfirmDeletePlan(true)}>Delete</button>
            </>
          )
        )}
        <div style={{ flex: 1 }} />
        {plan && (
          <div className="tabs" style={{ marginBottom: 0 }}>
            <button className={`tab${section === "rows" ? " active" : ""}`} onClick={() => setSection("rows")}>Rows</button>
            <button className={`tab${section === "workflow" ? " active" : ""}`} onClick={() => setSection("workflow")}>Workflow &amp; Pins</button>
          </div>
        )}
      </div>

      {plans.length === 0 && !showCreate && (
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

      {plan && section === "workflow" && (
        <WorkflowPinPanel
          key={plan.id + plan.updated_at}
          plan={plan}
          saving={updatePlanMutation.isPending}
          onSave={(patch) => updatePlanMutation.mutate(patch)}
        />
      )}

      {plan && section === "rows" && (
        <>
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
            onRunAll={() => runMutation.mutate({ planId: effectivePlanId!, rowIds: rows.map((r) => r.id) })}
            isRunning={isRunning}
            jobProgress={jobProgress}
          />

          <ComfyDefaultsStrip
            plan={plan}
            disabled={isRunning || updatePlanMutation.isPending}
            onSavePins={(pins) => updatePlanMutation.mutate({ pinned_params: pins })}
          />

          {/* Rows toolbar */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
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

          <ComfyRowsTable
            plan={plan}
            rows={rows}
            selected={selected}
            onToggle={toggleRow}
            onToggleAll={toggleAll}
            runningJob={isRunning}
          />
        </>
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
          rowIds={[...selected]}
          onClose={() => setShowSetFolder(false)}
        />
      )}

      {confirmDeletePlan && plan && (
        <ConfirmDialog
          title="Delete plan"
          message={`Delete plan "${plan.name}" and its ${rows.length} row${rows.length !== 1 ? "s" : ""}? Generated images already imported into the dataset are kept.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deletePlanMutation.mutate()}
          onCancel={() => setConfirmDeletePlan(false)}
        />
      )}

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
