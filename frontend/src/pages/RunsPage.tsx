import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import { comfyApi } from "../api/comfy";
import { providersApi } from "../api/providers";
import { runsApi, isLiveRun, HEAD_STAGES, RUN_STAGES, RUN_STAGE_LABELS } from "../api/runs";
import type { Run, RunPlanReport, RunRecipe, RunStage, RunStageKey, RunStartBody, StageStatus } from "../api/runs";
import RecipeBar from "../components/runs/RecipeBar";
import ExtendRunDialog from "../components/runs/ExtendRunDialog";
import { captionBackend } from "../constants/captionModels";
import RunCaptionFields from "../components/runs/RunCaptionFields";
import RunExportFields from "../components/runs/RunExportFields";
import DryRunReport from "../components/runs/DryRunReport";
import { captionModelId, type CaptionForm, type ExportForm } from "../components/runs/runForms";
import { RunGateFields, RunLoopFields, RunRewriteFields } from "../components/runs/RunLoopFields";
import type { GateForm, LoopForm, RewriteForm } from "../components/runs/runLoopForms";
import {
  NEEDS_STAGE, formToRecipe, hydrateRunForm, recipeToForm, toSavedRunForm, type PerDataset, type RunFormState,
} from "../components/runs/runRecipeForm";
import ServerStateGate from "../components/common/ServerStateGate";
import { RUN_FORM_PATH, appDataApi } from "../api/appData";
import { serverWritesSettled, writeToServer } from "../utils/serverWriter";
import { useDebouncedPersist } from "../hooks/useDebouncedPersist";
import ConfirmDialog from "../components/common/ConfirmDialog";
import { apiErrorDetail } from "../utils/apiError";

/**
 * Automated runs (M6 §4): one press fills a plan's blueprint, generates, scores,
 * captions and exports, stepping from stage to stage as each child job ends.
 *
 * Not keyed by `datasetId` in `PageRenderer`: it holds a job id for the progress
 * stream, so it re-seeds through the `prevDatasetId` effect instead, as ComfyPage does.
 */
export default function RunsPage() {
  const datasetId = usePaneDatasetId();
  const qc = useQueryClient();

  const { data: runs = [], isFetched } = useQuery({
    queryKey: ["runs", datasetId],
    queryFn: () => runsApi.list(datasetId!),
    enabled: !!datasetId,
    // The run is stepped by the worker after its child's terminal event, so the
    // event alone can arrive before the run row moves. Poll while one is running.
    refetchInterval: (q) => ((q.state.data ?? []).some((r) => r.status === "running") ? 1500 : false),
  });

  const active = runs.find(isLiveRun) ?? null;
  const history = runs.filter((r) => !isLiveRun(r));
  // "Use these settings" on a past run: handed to the start form, which clears it once
  // applied, so a form that mounts later never applies a stale one.
  const [loadRequest, setLoadRequest] = useState<LoadRequest | null>(null);

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h" style={{ marginBottom: 20 }}>
        <div>
          <h1>Runs</h1>
          <p>Fill a plan's folders, then generate, score, caption and export in one go.</p>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 980 }}>
        {datasetId && isFetched && (active
          ? <ActiveRun run={active} datasetId={datasetId} />
          : (
            // The form reads the remembered one in its state initializers, so it waits
            // for it (docs/dev/app-stored-data.md), after any write still on its way.
            <ServerStateGate
              queryKey={["app-data", "run-form"]}
              load={async () => {
                await serverWritesSettled(RUN_FORM_PATH);
                return appDataApi.getRunForm();
              }}
              label="your saved run form"
            >
              {(saved) => (
                <StartRunForm datasetId={datasetId} saved={saved}
                  onStarted={() => qc.invalidateQueries({ queryKey: ["runs", datasetId] })}
                  loadRequest={loadRequest} onLoaded={() => setLoadRequest(null)} />
              )}
            </ServerStateGate>
          ))}
        {datasetId && history.length > 0 && (
          <RunHistory runs={history} datasetId={datasetId}
            onUse={active ? undefined : (r) => setLoadRequest({ recipe: r.recipe, label: `the run "${r.name}"`, nonce: Date.now() })} />
        )}
      </div>
    </div>
  );
}

// ── Start form ───────────────────────────────────────────────────────────────

/** The provider id inside an `openai_compat:<id>:<model>` caption model, else null. */
function compatProviderId(model: string | null): string | null {
  return model?.startsWith("openai_compat:") ? model.split(":")[1] ?? null : null;
}

/** A recipe to fill the start form with, from outside it (a past run's settings). */
interface LoadRequest {
  recipe: RunRecipe;
  label: string;
  nonce: number;
}

function saveRunForm(path: string, value: object) {
  writeToServer(path, "PUT", { value });
}

function StartRunForm({ datasetId, saved, onStarted, loadRequest, onLoaded }: {
  datasetId: string; saved: unknown; onStarted: () => void; loadRequest: LoadRequest | null; onLoaded: () => void;
}) {
  // Seeded once from the remembered form (M6 §6); the form owns its state after that.
  const [initial] = useState(() => hydrateRunForm(saved));
  const [rawForm, setForm] = useState<RunFormState>(initial.form);
  // The plan and base folder are per dataset, so switching datasets shows that
  // dataset's own: they live here, keyed by dataset id, not in `rawForm`.
  const [perDataset, setPerDataset] = useState<PerDataset>(initial.byDataset);
  const here = perDataset[datasetId];
  const form: RunFormState = { ...rawForm, planId: here?.planId ?? null, subfolder: here?.subfolder ?? "" };
  const setDatasetEntry = (p: Partial<PerDataset[string]>) =>
    setPerDataset((b) => {
      // Re-inserted last, so the cap on remembered datasets drops the longest untouched.
      const { [datasetId]: cur, ...rest } = b;
      return { ...rest, [datasetId]: { ...(cur ?? { planId: null, subfolder: "" }), ...p } };
    });
  useDebouncedPersist(RUN_FORM_PATH, toSavedRunForm(rawForm, perDataset), undefined, saveRunForm);

  const patch = (p: Partial<RunFormState>) => setForm((f) => ({ ...f, ...p }));
  const { planId, name, subfolder, runAesthetic, aestheticModel, caption, exportForm, loop, gate, rewrite } = form;
  const stages = useMemo(() => new Set(rawForm.stages), [rawForm.stages]);
  const setPlanId = (v: string | null) => setDatasetEntry({ planId: v });
  const setName = (v: string) => patch({ name: v });
  const setSubfolder = (v: string) => setDatasetEntry({ subfolder: v });
  const setRunAesthetic = (v: boolean) => patch({ runAesthetic: v });
  const setAestheticModel = (v: "laion" | "v2_5") => patch({ aestheticModel: v });
  const setCaption = (v: CaptionForm) => patch({ caption: v });
  const setExportForm = (v: ExportForm) => patch({ exportForm: v });
  const setLoop = (v: LoopForm) => patch({ loop: v });
  const setGate = (v: GateForm) => patch({ gate: v });
  const setRewrite = (v: RewriteForm) => patch({ rewrite: v });
  const [dry, setDry] = useState<{ report: RunPlanReport; key: string } | null>(null);

  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    // Rewrite scenarios belong to the last dataset's plan; the provider and model stay.
    setForm((f) => ({ ...f, rewrite: { ...f.rewrite, scenarios: [] } }));
    setDry(null);
  }, [datasetId]);

  const { data: plans = [] } = useQuery({
    queryKey: ["comfy", "plans", datasetId],
    queryFn: () => comfyApi.listPlans(datasetId),
  });
  // A plan id from a loaded recipe or another dataset is not trusted: the start would
  // 400 "belongs to another dataset" with nothing on screen saying why.
  const effectivePlanId = (planId && plans.some((p) => p.id === planId) ? planId : plans[0]?.id) ?? null;
  const { data: blueprint } = useQuery({
    queryKey: ["comfy", "blueprint", effectivePlanId],
    queryFn: () => comfyApi.getBlueprint(effectivePlanId!),
    enabled: !!effectivePlanId,
  });
  const captionModel = captionModelId(caption);
  const { data: providers = [] } = useQuery({ queryKey: ["providers"], queryFn: providersApi.list });
  const rewriteScenarios = (blueprint?.spec.scenarios ?? []).filter((sc) => sc.rewrite).map((sc) => sc.name);
  // Ticks for another plan's scenarios (the plan picker changed) are not sent.
  const tickedScenarios = rewrite.scenarios.filter((sc) => rewriteScenarios.includes(sc));
  const rewriteProviderId = rewrite.providerId || providers[0]?.id || "";
  const rewriteOn = stages.has("rewrite") && stages.has("prompts");
  const billed = (id: string | null) => !!id && !!providers.find((p) => p.id === id)?.is_billed;
  // The paid-call cap applies only when a billed provider rewrites or captions.
  const paidCap = (rewriteOn && billed(rewriteProviderId)) || (stages.has("caption") && billed(compatProviderId(captionModel)));

  /** Fill the form from a stored recipe, keeping this form's plan and run name. What
   *  the recipe names that is not here (a provider, an export folder) is cleared and
   *  said in a toast. */
  const applyRecipe = (recipe: RunRecipe, label: string) => {
    const { form: next, notes } = recipeToForm(recipe, { planId, name }, {
      providerIds: providers.map((p) => p.id),
      rewriteScenarios: blueprint ? rewriteScenarios : null,
    });
    setForm(next);
    setDatasetEntry({ subfolder: next.subfolder });
    setDry(null);
    toast.success(`Filled the form from ${label}`);
    for (const n of notes) toast(n, { duration: 8000 });
  };
  // The latest `applyRecipe`, for the effect below, which must fire on a new request only.
  const applyRef = useRef(applyRecipe);
  useEffect(() => { applyRef.current = applyRecipe; });
  useEffect(() => {
    if (!loadRequest) return;
    applyRef.current(loadRequest.recipe, loadRequest.label);
    onLoaded();
  }, [loadRequest, onLoaded]);

  const toggle = (k: RunStageKey) =>
    setForm((f) => ({ ...f, stages: f.stages.includes(k) ? f.stages.filter((s) => s !== k) : [...f.stages, k] }));

  const blockers: string[] = [];
  if (!effectivePlanId) blockers.push("Create a plan on the ComfyUI page first.");
  if (stages.size === 0) blockers.push("Switch on at least one stage.");
  if (stages.has("caption") && (!captionModel || captionBackend(captionModel) === null)) blockers.push("Choose a caption model.");
  if (stages.has("export") && !exportForm.outputDir.trim()) blockers.push("Choose an export folder.");
  if (loop.on && !(stages.has("prompts") && stages.has("generate"))) blockers.push("Repeating needs Fill the blueprint and Generate.");
  if (loop.on && !blueprint) blockers.push("Repeating needs a blueprint on the plan.");
  if (stages.has("gate") && stages.has("score") && gate.flags.length === 0 && !(gate.aestheticOn && runAesthetic)) {
    blockers.push("Give the gate something to test.");
  }
  if (rewriteOn && !rewriteProviderId) blockers.push("Add an LLM provider in Settings for the rewrite.");
  if (rewriteOn && tickedScenarios.length === 0) blockers.push("Tick the scenarios the rewrite may change.");

  // The one request Start and Dry run both send, so the report is about this run.
  const body = (): RunStartBody => ({
    dataset_id: datasetId,
    plan_id: effectivePlanId!,
    name: name.trim(),
    recipe: formToRecipe(form, { rewriteProviderId, tickedScenarios, paidCap }),
  });
  const bodyKey = blockers.length === 0 ? JSON.stringify(body()) : null;

  const dryRun = useMutation({
    mutationFn: (b: RunStartBody) => runsApi.dryRun(b),
    onSuccess: (report, b) => setDry({ report, key: JSON.stringify(b) }),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not check the run")),
  });

  const start = useMutation({
    mutationFn: () => runsApi.start(body()),
    onSuccess: (run) => {
      // The start answers 200 for a run whose first stage could not start: say so.
      if (run.status === "failed") toast.error(run.error_msg ?? "The run failed at its first stage");
      else if (run.status === "paused") toast("Run paused — see its first stage");
      else if (run.status === "completed") toast.success("Run finished — every stage was skipped or done");
      else toast.success("Run started");
      onStarted();
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not start the run")),
  });

  return (
    <div className="panel">
      <div className="panel-h"><h3>Start a run</h3></div>
      <div style={{ padding: "4px 22px" }}>
        <RecipeBar
          buildRecipe={() => formToRecipe(form, { rewriteProviderId, tickedScenarios, paidCap })}
          onLoad={(r) => applyRecipe(r.recipe, `the recipe "${r.name}"`)}
        />
        <div className="form-row">
          <div className="lbl-col"><h4>Plan</h4><p>The ComfyUI plan whose rows this run generates.</p></div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <select className="select" value={effectivePlanId ?? ""} onChange={(e) => setPlanId(e.target.value || null)}>
              {plans.length === 0 && <option value="">No plans in this dataset</option>}
              {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <span style={{ fontSize: 11.5, color: "var(--fg-mute)" }}>
              {blueprint
                ? "This plan has a blueprint: the run fills every folder that is short first."
                : "No blueprint: the run generates the plan's pending rows."}
            </span>
            <input className="input" placeholder="Run name (optional)" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
          </div>
        </div>

        <div className="form-row">
          <div className="lbl-col"><h4>Stages</h4><p>They always run in this order. Switch off any you do not want.</p></div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {RUN_STAGES.map((k) => {
              const after = NEEDS_STAGE[k];
              const off = !!after && !stages.has(after);
              return (
                <label key={k} className="row-flex" style={{ gap: 8, paddingLeft: after ? 22 : 0, opacity: off ? 0.5 : 1 }}
                  title={off ? `Only runs with ${RUN_STAGE_LABELS[after!]}` : undefined}>
                  <input type="checkbox" className="checkbox" checked={stages.has(k)} disabled={off} onChange={() => toggle(k)} />
                  <span style={{ fontSize: 12.5 }}>{RUN_STAGE_LABELS[k]}</span>
                </label>
              );
            })}
          </div>
        </div>

        {stages.has("generate") && (
          <div className="form-row">
            <div className="lbl-col"><h4>Generate</h4><p>A base folder for the outputs; each row's own folder goes inside it.</p></div>
            <input className="input" placeholder="Base folder (optional)" value={subfolder} onChange={(e) => setSubfolder(e.target.value)} />
          </div>
        )}

        {stages.has("score") && (
          <div className="form-row">
            <div className="lbl-col"><h4>Score</h4><p>Technical scores always run; they flag duplicates.</p></div>
            <div className="row-flex" style={{ gap: 10, flexWrap: "wrap" }}>
              <label className="row-flex" style={{ gap: 8 }}>
                <input type="checkbox" className="checkbox" checked={runAesthetic} onChange={(e) => setRunAesthetic(e.target.checked)} />
                <span style={{ fontSize: 12.5 }}>Aesthetic score</span>
              </label>
              <select className="select" value={aestheticModel} disabled={!runAesthetic} onChange={(e) => setAestheticModel(e.target.value as "laion" | "v2_5")}>
                <option value="laion">LAION</option>
                <option value="v2_5">V2.5</option>
              </select>
            </div>
          </div>
        )}

        {stages.has("gate") && stages.has("score") && <RunGateFields value={gate} onChange={setGate} runAesthetic={runAesthetic} />}

        {rewriteOn && (
          <RunRewriteFields value={{ ...rewrite, providerId: rewriteProviderId }} onChange={setRewrite}
            providers={providers} scenarios={rewriteScenarios} />
        )}

        <RunLoopFields value={loop} onChange={setLoop} paidCap={paidCap} />

        {stages.has("caption") && <RunCaptionFields value={caption} onChange={setCaption} />}

        {stages.has("export") && <RunExportFields value={exportForm} onChange={setExportForm} datasetId={datasetId} />}

        <div className="row-flex" style={{ gap: 12, padding: "14px 0" }}>
          <button className="btn primary" disabled={blockers.length > 0 || start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? "Starting…" : "Start run"}
          </button>
          <button className="btn" disabled={blockers.length > 0 || dryRun.isPending} onClick={() => dryRun.mutate(body())}>
            {dryRun.isPending ? "Checking…" : "Dry run"}
          </button>
          {blockers.length > 0 && <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>{blockers[0]}</span>}
        </div>
        {dry && <DryRunReport report={dry.report} stale={dry.key !== bodyKey} onClose={() => setDry(null)} />}
      </div>
    </div>
  );
}

// ── The active run ───────────────────────────────────────────────────────────

const STAGE_COLOURS: Record<StageStatus, string> = {
  pending: "var(--fg-dim)",
  running: "var(--accent)",
  done: "var(--good)",
  skipped: "var(--fg-mute)",
  failed: "var(--bad)",
  cancelled: "var(--warn)",
};

function ActiveRun({ run, datasetId }: { run: Run; datasetId: string }) {
  const qc = useQueryClient();
  useJobSSE(run.current_job_id);
  const progress = useJobStore((s) => (run.current_job_id ? s.activeJobs.get(run.current_job_id) : undefined));
  const [confirmCancel, setConfirmCancel] = useState(false);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["runs", datasetId] });
  };

  // The child's terminal event can arrive before the worker steps the run, so
  // refetch on it as well as on the poll.
  const childStatus = progress?.status;
  useEffect(() => {
    if (childStatus && ["completed", "failed", "cancelled"].includes(childStatus)) {
      qc.invalidateQueries({ queryKey: ["runs", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["subfolders", datasetId] });
      if (run.plan_id) qc.invalidateQueries({ queryKey: ["comfy", "rows", run.plan_id] });
    }
  }, [childStatus, datasetId, qc, run.plan_id]);

  const act = useMutation({
    mutationFn: (kind: "pause" | "resume" | "cancel") => runsApi[kind](run.id),
    onSuccess: refresh,
    onError: (err) => toast.error(apiErrorDetail(err, "That did not work")),
  });

  const running = run.status === "running";
  // A cancel asked for during the snapshot waits for it: the run is still `running`.
  const cancelling = run.cancel_requested;
  const state = cancelling
    ? "cancelling after the snapshot"
    : running ? (run.pause_requested ? "pausing after this stage" : "running") : "paused";
  return (
    <div className="panel">
      <div className="panel-h">
        <h3>{run.name}</h3>
        <span style={{ fontSize: 12, color: running && !cancelling ? "var(--accent)" : "var(--warn)", marginLeft: 10 }}>
          {state}
        </span>
        <div style={{ flex: 1 }} />
        {running && !run.pause_requested && !cancelling && (
          <button className="btn sm ghost" disabled={act.isPending} onClick={() => act.mutate("pause")}>Pause</button>
        )}
        {(!running || run.pause_requested) && !cancelling && (
          <button className="btn sm primary" disabled={act.isPending} onClick={() => act.mutate("resume")}>Resume</button>
        )}
        <button className="btn sm ghost" style={{ color: "var(--bad)" }} disabled={act.isPending || cancelling} onClick={() => setConfirmCancel(true)}>Cancel</button>
      </div>
      <div style={{ padding: "8px 22px 16px" }}>
        <StageList run={run} />
        {progress && progress.status === "running" && <ChildProgress done={progress.done} total={progress.total} message={progress.message} />}
      </div>
      {confirmCancel && (
        <ConfirmDialog
          title="Cancel this run?"
          message={run.stages.some((s) => s.key === "snapshot" && s.status === "running")
            ? "The snapshot cannot stop partway, so the run stops once it is recorded. No later stage starts."
            : "The job it is running now is cancelled, and no later stage starts. Images already made stay."}
          confirmLabel="Cancel run"
          danger
          onConfirm={() => { setConfirmCancel(false); act.mutate("cancel"); }}
          onCancel={() => setConfirmCancel(false)}
        />
      )}
    </div>
  );
}

/** The current stage's progress: one count and a bar. A job's own message (such as
 *  "Generating 3/84") names the item it is *on*, one ahead of the finished count, so
 *  showing both read as two numbers that disagree. The message is used only when the
 *  job reports no total (an export counts nothing until it is done). */
function ChildProgress({ done, total, message }: { done: number; total: number; message?: string }) {
  if (!total) {
    return <div style={{ marginTop: 12, fontSize: 12.5, color: "var(--fg-mute)" }}>{message ?? "Working…"}</div>;
  }
  const pct = Math.min(100, Math.max(0, (done / total) * 100));
  return (
    <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6, maxWidth: 420 }}>
      <span style={{ fontSize: 12.5, color: "var(--fg)" }}>
        {done.toLocaleString()} of {total.toLocaleString()} done
      </span>
      <div
        role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}
        style={{ height: 6, borderRadius: 3, background: "var(--surface-2)", overflow: "hidden" }}
      >
        <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent)", transition: "width .3s" }} />
      </div>
    </div>
  );
}

/** A heading before each pass of a looping run, and before the stages before the first
 *  pass and after the last. A head stage (the snapshot) carries no pass number either,
 *  so it is told apart from the tail by its key. */
function passHeading(stages: RunStage[], i: number, loops: boolean): string | null {
  if (!loops) return null;
  const group = (s: RunStage) => (HEAD_STAGES.includes(s.key) ? "head" : s.pass_no ?? null);
  const here = group(stages[i]);
  const before = i > 0 ? group(stages[i - 1]) : undefined;
  if (here === before) return null;
  if (here === "head") return "Before the first pass";
  return here === null ? "After the last pass" : `Pass ${here}`;
}

function StageList({ run }: { run: Run }) {
  const loops = !!run.recipe.loop;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {run.stages.map((s, i) => {
        const heading = passHeading(run.stages, i, loops);
        return (
          <div key={`${i}:${s.key}`} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {heading && <div style={{ fontSize: 11, fontWeight: 600, color: "var(--fg-dim)", marginTop: i > 0 ? 6 : 0 }}>{heading}</div>}
            <div className="row-flex" style={{ gap: 10, alignItems: "baseline" }}>
              <span style={{ width: 70, fontSize: 11, textTransform: "uppercase", letterSpacing: ".04em", color: STAGE_COLOURS[s.status] }}>{s.status}</span>
              <span style={{ fontSize: 12.5, minWidth: 160 }}>{RUN_STAGE_LABELS[s.key] ?? s.key}</span>
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>{[s.summary, s.note].filter(Boolean).join(" · ")}</span>
            </div>
          </div>
        );
      })}
      {run.error_msg && <div style={{ fontSize: 12, color: "var(--bad)", marginTop: 4 }}>{run.error_msg}</div>}
      {loops && <LoopOutcome run={run} />}
    </div>
  );
}

/** How a looping run ended, and the folders still short: met, or why it stopped. */
function LoopOutcome({ run }: { run: Run }) {
  const short = run.leaves.filter((l) => l.state !== "met");
  const met = run.met_count;
  if (!run.outcome && short.length === 0) return null;
  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
      {run.outcome && (
        <div style={{ fontSize: 12.5, color: run.outcome.outcome === "met" ? "var(--good)" : "var(--warn)" }}>
          {run.outcome.outcome === "met"
            ? `Every folder is full (${run.leaf_count}).`
            : `${met} of ${run.leaf_count} folders full. Stopped: ${run.outcome.reason ?? "some folders are short"}.`}
        </div>
      )}
      {short.slice(0, 20).map((l) => (
        <div key={l.path} style={{ fontSize: 12, color: "var(--fg-mute)", paddingLeft: 12 }}>
          {l.path}: {l.kept} of {l.target}{l.state === "abandoned" ? ` — given up (${l.reason ?? "no gain"})` : ""}
        </div>
      ))}
      {run.leaf_count - met > 20 && (
        <div style={{ fontSize: 12, color: "var(--fg-mute)", paddingLeft: 12 }}>…and {run.leaf_count - met - 20} more</div>
      )}
    </div>
  );
}

// ── History ──────────────────────────────────────────────────────────────────

function RunHistory({ runs, datasetId, onUse }: {
  runs: Run[]; datasetId: string;
  /** Fill the start form from a run's recipe; absent while a run is live (no form shows). */
  onUse?: (r: Run) => void;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [extending, setExtending] = useState<Run | null>(null);
  const act = useMutation({
    mutationFn: async ({ kind, id }: { kind: "resume" | "remove"; id: string }): Promise<void> => {
      if (kind === "resume") await runsApi.resume(id);
      else await runsApi.remove(id);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs", datasetId] }),
    onError: (err) => toast.error(apiErrorDetail(err, "That did not work")),
  });
  return (
    <div className="panel">
      <div className="panel-h"><h3>Past runs</h3></div>
      <div style={{ padding: "4px 22px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
        {runs.map((r) => (
          <div key={r.id} style={{ borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>
            <div className="row-flex" style={{ gap: 10 }}>
              <button className="btn sm ghost" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? "▾" : "▸"}</button>
              <span style={{ fontSize: 12.5, flex: 1 }}>{r.name}</span>
              <span style={{ fontSize: 12, color: r.status === "completed" ? "var(--good)" : r.status === "failed" ? "var(--bad)" : "var(--fg-mute)" }}>{r.status}</span>
              {r.can_extend && onUse && (
                <button className="btn sm ghost" title="Raise the cap that stopped this run, and carry on"
                  onClick={() => setExtending(r)}>
                  Raise caps and continue
                </button>
              )}
              {onUse && (
                <button className="btn sm ghost" title="Fill the start form with this run's settings" onClick={() => onUse(r)}>
                  Use these settings
                </button>
              )}
              {r.status === "failed" && (
                <button className="btn sm ghost" disabled={act.isPending} onClick={() => act.mutate({ kind: "resume", id: r.id })}>Resume</button>
              )}
              <button className="btn sm ghost" disabled={act.isPending} onClick={() => act.mutate({ kind: "remove", id: r.id })}>Delete</button>
            </div>
            {open === r.id && <div style={{ padding: "8px 0 0 40px" }}><StageList run={r} /></div>}
          </div>
        ))}
      </div>
      {extending && (
        <ExtendRunDialog run={extending} onClose={() => setExtending(null)}
          onDone={() => { setExtending(null); qc.invalidateQueries({ queryKey: ["runs", datasetId] }); }} />
      )}
    </div>
  );
}
