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

function apiErrorDetail(err: unknown, fallback: string): string {
  return (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
}

export default function ComfyPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();

  const [planId, setPlanId] = useState<string | null>(null);
  const [section, setSection] = useState<"rows" | "workflow">("rows");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [subfolder, setSubfolder] = useState("");
  const [setCaption, setSetCaption] = useState(true);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  // Modals / inline forms
  const [showCreate, setShowCreate] = useState(false);
  const [newPlanName, setNewPlanName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDeletePlan, setConfirmDeletePlan] = useState(false);
  const [confirmDeleteRows, setConfirmDeleteRows] = useState(false);
  const [showPaste, setShowPaste] = useState(false);
  const [pasteText, setPasteText] = useState("");

  // Reset local state when the dataset changes without a remount (pane mode).
  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    setPlanId(null);
    setSelected(new Set());
    setSubfolder("");
    setActiveJobId(null);
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

  useJobSSE(activeJobId);
  const jobProgress = useJobStore((s) => s.activeJobs.get(activeJobId ?? ""));

  useEffect(() => {
    if (!jobProgress) return;
    if (jobProgress.status === "running") {
      // Refresh row statuses as the run progresses (cheap list query, ~1 refetch/row).
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
    }
    if (["completed", "failed", "cancelled"].includes(jobProgress.status)) {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      if (jobProgress.status === "completed") toast.success("Generation run finished");
      else if (jobProgress.status === "failed") toast.error("Generation run failed — see row errors / Logs");
      setActiveJobId(null);
    }
  }, [jobProgress?.status, jobProgress?.done, effectivePlanId, datasetId, qc]); // eslint-disable-line react-hooks/exhaustive-deps

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
    mutationFn: (lines: string[]) => comfyApi.bulkAddRows(effectivePlanId!, lines),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
      qc.invalidateQueries({ queryKey: ["comfy", "plans", datasetId] });
      setShowPaste(false);
      setPasteText("");
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

  const runMutation = useMutation({
    mutationFn: (rowIds?: string[]) =>
      comfyApi.run({
        plan_id: effectivePlanId!,
        row_ids: rowIds,
        subfolder,
        set_caption: setCaption,
      }),
    onSuccess: (data) => {
      setActiveJobId(data.job_id);
      toast.success(`Generation started — ${data.total} row${data.total !== 1 ? "s" : ""}`);
      qc.invalidateQueries({ queryKey: ["comfy", "rows", effectivePlanId] });
    },
    onError: (err: unknown) => toast.error(apiErrorDetail(err, "Failed to start generation")),
  });

  // ── Derived ────────────────────────────────────────────────────────────────

  const pendingCount = rows.filter((r) => r.status === "pending").length;
  const failedCount = rows.filter((r) => r.status === "failed").length;
  const isRunning = runMutation.isPending || !!activeJobId;

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
            onChange={(e) => { setPlanId(e.target.value); setSelected(new Set()); }}
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
            subfolder={subfolder}
            onSubfolderChange={setSubfolder}
            setCaption={setCaption}
            onSetCaptionChange={setSetCaption}
            onRunPending={() => runMutation.mutate(undefined)}
            onRunSelected={() => runMutation.mutate([...selected])}
            isRunning={isRunning}
            jobProgress={jobProgress}
          />

          {/* Rows toolbar */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <button className="btn ghost sm" onClick={() => addRowMutation.mutate()} disabled={plan.pinned_params.length === 0}>+ Add row</button>
            <button className="btn ghost sm" onClick={() => setShowPaste(true)}
              disabled={!plan.pinned_params.some((p) => p.is_prompt)}
              title={plan.pinned_params.some((p) => p.is_prompt) ? "Add one row per line of prompt text" : "Mark a pinned parameter as the prompt first"}>
              Paste prompts…
            </button>
            <div style={{ flex: 1 }} />
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
            <div className="panel-h"><h3>Paste prompts</h3></div>
            <div className="panel-b">
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 8px" }}>
                One prompt per line — each becomes a row with that prompt; other parameters use the template values.
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
                  onClick={() => bulkAddMutation.mutate(pasteText.split("\n"))}
                >
                  Add {pasteText.split("\n").filter((l) => l.trim()).length} rows
                </button>
              </div>
            </div>
          </div>
        </div>
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
