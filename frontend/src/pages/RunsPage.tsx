import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { FolderOpen } from "lucide-react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { useJobSSE } from "../hooks/useSSE";
import { useJobStore } from "../store/jobStore";
import { comfyApi } from "../api/comfy";
import { captioningApi } from "../api/captioning";
import { providersApi } from "../api/providers";
import { runsApi, isLiveRun, RUN_STAGES, RUN_STAGE_LABELS } from "../api/runs";
import type { Run, RunRecipe, RunStageKey, StageStatus } from "../api/runs";
import { captionBackend, captionModelOptions } from "../constants/captionModels";
import DirPickerModal from "../components/common/DirPickerModal";
import ModelPicker from "../components/providers/ModelPicker";
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
          : <StartRunForm datasetId={datasetId} onStarted={() => qc.invalidateQueries({ queryKey: ["runs", datasetId] })} />)}
        {datasetId && history.length > 0 && <RunHistory runs={history} datasetId={datasetId} />}
      </div>
    </div>
  );
}

// ── Start form ───────────────────────────────────────────────────────────────

const DEFAULT_STAGES: RunStageKey[] = ["prompts", "handoff_to_comfy", "generate", "handoff_to_local", "score"];

function StartRunForm({ datasetId, onStarted }: { datasetId: string; onStarted: () => void }) {
  const [planId, setPlanId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [stages, setStages] = useState<Set<RunStageKey>>(new Set(DEFAULT_STAGES));
  const [subfolder, setSubfolder] = useState("");
  const [runAesthetic, setRunAesthetic] = useState(true);
  const [aestheticModel, setAestheticModel] = useState<"laion" | "v2_5">("laion");
  const [captionBase, setCaptionBase] = useState("");
  const [providerModel, setProviderModel] = useState("");
  const [overwrite, setOverwrite] = useState(true);
  const [exportKind, setExportKind] = useState<"plain" | "kohya" | "aitoolkit">("kohya");
  const [outputDir, setOutputDir] = useState("");
  const [nRepeats, setNRepeats] = useState(10);
  const [concept, setConcept] = useState("concept");
  const [excludeDupes, setExcludeDupes] = useState(true);
  const [dirPicker, setDirPicker] = useState(false);

  const prevDatasetId = useRef(datasetId);
  useEffect(() => {
    if (datasetId === prevDatasetId.current) return;
    prevDatasetId.current = datasetId;
    setPlanId(null);
    setSubfolder("");
  }, [datasetId]);

  const { data: plans = [] } = useQuery({
    queryKey: ["comfy", "plans", datasetId],
    queryFn: () => comfyApi.listPlans(datasetId),
  });
  const effectivePlanId = planId ?? plans[0]?.id ?? null;
  const { data: blueprint } = useQuery({
    queryKey: ["comfy", "blueprint", effectivePlanId],
    queryFn: () => comfyApi.getBlueprint(effectivePlanId!),
    enabled: !!effectivePlanId,
  });
  const { data: modelsData } = useQuery({
    queryKey: ["captioning-models"],
    queryFn: captioningApi.models,
    enabled: stages.has("caption"),
  });
  const { data: providers = [] } = useQuery({
    queryKey: ["providers"],
    queryFn: providersApi.list,
    staleTime: 30_000,
    enabled: stages.has("caption"),
  });
  const modelOptions = useMemo(() => captionModelOptions(modelsData, providers), [modelsData, providers]);
  const groups = useMemo(() => {
    const out = new Map<string, typeof modelOptions>();
    for (const o of modelOptions) out.set(o.group, [...(out.get(o.group) ?? []), o]);
    return [...out.entries()];
  }, [modelOptions]);
  const isProvider = captionBase.startsWith("openai_compat:");
  const selectedProvider = isProvider ? providers.find((p) => captionBase === `openai_compat:${p.id}`) : undefined;
  const captionModel = isProvider && providerModel.trim() ? `${captionBase}:${providerModel.trim()}` : captionBase;

  const toggle = (k: RunStageKey) =>
    setStages((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });

  const blockers: string[] = [];
  if (!effectivePlanId) blockers.push("Create a plan on the ComfyUI page first.");
  if (stages.size === 0) blockers.push("Switch on at least one stage.");
  if (stages.has("caption") && (!captionModel || captionBackend(captionModel) === null)) blockers.push("Choose a caption model.");
  if (stages.has("export") && !outputDir.trim()) blockers.push("Choose an export folder.");

  const start = useMutation({
    mutationFn: () => {
      const recipe: RunRecipe = {
        stages: RUN_STAGES.filter((s) => stages.has(s)),
        generate: { subfolder: subfolder.trim(), set_caption: true },
        score: { run_aesthetic: runAesthetic, aesthetic_model: aestheticModel },
        caption: stages.has("caption")
          ? { model: captionModel, style: "detailed", custom_prompt: "", overwrite }
          : null,
        export: stages.has("export")
          ? {
              kind: exportKind, output_dir: outputDir.trim(), n_repeats: nRepeats,
              concept_token: concept, concept_name: concept,
              captioned_only: false, exclude_duplicates: excludeDupes,
            }
          : null,
      };
      return runsApi.start({ dataset_id: datasetId, plan_id: effectivePlanId!, name: name.trim(), recipe });
    },
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
            {RUN_STAGES.map((k) => (
              <label key={k} className="row-flex" style={{ gap: 8 }}>
                <input type="checkbox" className="checkbox" checked={stages.has(k)} onChange={() => toggle(k)} />
                <span style={{ fontSize: 12.5 }}>{RUN_STAGE_LABELS[k]}</span>
              </label>
            ))}
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

        {stages.has("caption") && (
          <div className="form-row">
            <div className="lbl-col"><h4>Caption</h4><p>Only this run's own images are captioned.</p></div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <select className="select" value={captionBase} onChange={(e) => {
                const v = e.target.value;
                setCaptionBase(v);
                const p = providers.find((pr) => v === `openai_compat:${pr.id}`);
                setProviderModel(p?.default_model ?? "");
              }}>
                <option value="">— choose a model —</option>
                {groups.map(([group, opts]) => (
                  <optgroup key={group} label={group}>
                    {opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </optgroup>
                ))}
              </select>
              {isProvider && selectedProvider && (
                // The same picker CaptioningPage uses: it asks the provider for its models.
                <ModelPicker
                  key={selectedProvider.id}
                  value={providerModel}
                  onChange={setProviderModel}
                  providerId={selectedProvider.id}
                  baseUrl={selectedProvider.base_url}
                  placeholder={`Model name (default: ${selectedProvider.default_model || "none"})`}
                />
              )}
              <label className="row-flex" style={{ gap: 8 }}>
                <input type="checkbox" className="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
                <span style={{ fontSize: 12.5 }}>Replace existing captions (the generate stage writes each prompt as one)</span>
              </label>
            </div>
          </div>
        )}

        {stages.has("export") && (
          <div className="form-row">
            <div className="lbl-col"><h4>Export</h4><p>Exports the whole dataset through these settings.</p></div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div className="row-flex" style={{ flexWrap: "wrap" }}>
                {([["kohya", "kohya"], ["aitoolkit", "ai-toolkit"], ["plain", "plain folder"]] as const).map(([v, label]) => (
                  <button key={v} className={`btn sm${exportKind === v ? " primary" : ""}`} onClick={() => setExportKind(v)}>{label}</button>
                ))}
              </div>
              <div className="row-flex" style={{ gap: 6 }}>
                <input className="input" style={{ flex: 1 }} placeholder="Output folder (absolute path)" value={outputDir} onChange={(e) => setOutputDir(e.target.value)} />
                <button className="btn sm ghost" onClick={() => setDirPicker(true)} title="Browse"><FolderOpen size={14} /></button>
              </div>
              {exportKind !== "plain" && (
                <div className="row-flex" style={{ gap: 8 }}>
                  {exportKind === "kohya" && (
                    <label className="row-flex" style={{ gap: 6 }}>
                      <span style={{ fontSize: 12.5 }}>Repeats</span>
                      <input type="number" className="input" min={1} max={1000} value={nRepeats}
                        onChange={(e) => setNRepeats(Math.max(1, Math.min(1000, Number(e.target.value) || 1)))} style={{ width: 70 }} />
                    </label>
                  )}
                  <label className="row-flex" style={{ gap: 6 }}>
                    <span style={{ fontSize: 12.5 }}>{exportKind === "kohya" ? "Concept token" : "Concept name"}</span>
                    <input className="input" value={concept} maxLength={200} onChange={(e) => setConcept(e.target.value)} />
                  </label>
                </div>
              )}
              <label className="row-flex" style={{ gap: 8 }}>
                <input type="checkbox" className="checkbox" checked={excludeDupes} onChange={(e) => setExcludeDupes(e.target.checked)} />
                <span style={{ fontSize: 12.5 }}>Leave out duplicates</span>
              </label>
            </div>
            {dirPicker && (
              <DirPickerModal initialPath={outputDir}
                onConfirm={(p) => { setOutputDir(p); setDirPicker(false); }}
                onCancel={() => setDirPicker(false)} />
            )}
          </div>
        )}

        <div className="row-flex" style={{ gap: 12, padding: "14px 0" }}>
          <button className="btn primary" disabled={blockers.length > 0 || start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? "Starting…" : "Start run"}
          </button>
          {blockers.length > 0 && <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>{blockers[0]}</span>}
        </div>
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
  return (
    <div className="panel">
      <div className="panel-h">
        <h3>{run.name}</h3>
        <span style={{ fontSize: 12, color: running ? "var(--accent)" : "var(--warn)", marginLeft: 10 }}>
          {running ? (run.pause_requested ? "pausing after this stage" : "running") : "paused"}
        </span>
        <div style={{ flex: 1 }} />
        {running && !run.pause_requested && (
          <button className="btn sm ghost" disabled={act.isPending} onClick={() => act.mutate("pause")}>Pause</button>
        )}
        {(!running || run.pause_requested) && (
          <button className="btn sm primary" disabled={act.isPending} onClick={() => act.mutate("resume")}>Resume</button>
        )}
        <button className="btn sm ghost" style={{ color: "var(--bad)" }} disabled={act.isPending} onClick={() => setConfirmCancel(true)}>Cancel</button>
      </div>
      <div style={{ padding: "8px 22px 16px" }}>
        <StageList run={run} />
        {progress && progress.status === "running" && <ChildProgress done={progress.done} total={progress.total} message={progress.message} />}
      </div>
      {confirmCancel && (
        <ConfirmDialog
          title="Cancel this run?"
          message="The job it is running now is cancelled, and no later stage starts. Images already made stay."
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

function StageList({ run }: { run: Run }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {run.stages.map((s, i) => (
        <div key={`${i}:${s.key}`} className="row-flex" style={{ gap: 10, alignItems: "baseline" }}>
          <span style={{ width: 70, fontSize: 11, textTransform: "uppercase", letterSpacing: ".04em", color: STAGE_COLOURS[s.status] }}>{s.status}</span>
          <span style={{ fontSize: 12.5, minWidth: 160 }}>{RUN_STAGE_LABELS[s.key] ?? s.key}</span>
          <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>{[s.summary, s.note].filter(Boolean).join(" · ")}</span>
        </div>
      ))}
      {run.error_msg && <div style={{ fontSize: 12, color: "var(--bad)", marginTop: 4 }}>{run.error_msg}</div>}
    </div>
  );
}

// ── History ──────────────────────────────────────────────────────────────────

function RunHistory({ runs, datasetId }: { runs: Run[]; datasetId: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
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
              {r.status === "failed" && (
                <button className="btn sm ghost" disabled={act.isPending} onClick={() => act.mutate({ kind: "resume", id: r.id })}>Resume</button>
              )}
              <button className="btn sm ghost" disabled={act.isPending} onClick={() => act.mutate({ kind: "remove", id: r.id })}>Delete</button>
            </div>
            {open === r.id && <div style={{ padding: "8px 0 0 40px" }}><StageList run={r} /></div>}
          </div>
        ))}
      </div>
    </div>
  );
}
