import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import toast from "react-hot-toast";
import { comfyApi } from "../../api/comfy";
import { jobsApi } from "../../api/jobs";
import { providersApi } from "../../api/providers";
import { useJobStore } from "../../store/jobStore";
import ModelPicker from "../providers/ModelPicker";
import JobProgressBar from "../common/JobProgressBar";
import { loadPersisted, savePersisted } from "../../utils/persistentState";

/** Body of the durable "generate until N" job (see comfyApi.generatePromptsJob). */
export interface GeneratePromptsJobBody {
  provider_id: string;
  model_name: string;
  system_instructions: string;
  instruction: string;
  batch_size: number;
  temperature: number;
  target_count: number;
  use_existing_context: boolean;
}

interface Props {
  planId: string;
  onAdd: (prompts: string[]) => void;
  adding: boolean;
  /** Starts the background "generate until N" job (mutation lives in ComfyPage). */
  onStartJob: (body: GeneratePromptsJobBody) => void;
  startingJob: boolean;
  /** Job id returned by that mutation, so this modal can attach to a job it started. */
  startedJobId: string | null;
  onClose: () => void;
}

/** Standing settings worth keeping between sessions, per plan. */
interface PersistedGenSettings {
  instructions: string;
  providerId: string;
  model: string;
  batchSize: number;
  temperature: number;
  targetCount: number;
}

const GEN_DEFAULTS: PersistedGenSettings = {
  instructions: "", providerId: "", model: "", batchSize: 5, temperature: 0.9, targetCount: 20,
};

const TERMINAL = ["completed", "failed", "cancelled"];

/** LLM prompt generation: small batches, each pushed to diverge from everything
    already generated/queued.

    Two paths, deliberately different in kind. *Generate N more* is synchronous
    and stages into the review textarea — edit, then **Add N rows**. *Generate
    until N* is a background job (`comfy_prompts`) that writes rows itself, per
    batch: closing this modal, navigating away or reloading no longer discards
    calls already paid for, which is the entire point of the split. */
export default function GeneratePromptsModal({
  planId, onAdd, adding, onStartJob, startingJob, startedJobId, onClose,
}: Props) {
  const { data: providers = [] } = useQuery({ queryKey: ["providers"], queryFn: providersApi.list });

  // The queue's existing prompts, read from the server rather than derived from
  // rows here. A row's prompt is row value → pin run default → workflow template
  // (`effective_prompt`), so a row with empty `values` still runs with a prompt
  // and still counts. Deriving it client-side gave a different number than the
  // job endpoint's own gate, which offered runs the server then rejected with a
  // 400 — this count and that gate must be the same number.
  const { data: planPrompts = [], isPending: promptsLoading } = useQuery({
    queryKey: ["comfy", "prompts", planId],
    queryFn: () => comfyApi.listPlanPrompts(planId),
    // Overrides the global 30 s staleTime (App.tsx): a cached count gates a
    // server-validated action, so it must be re-read every time this opens.
    staleTime: 0,
  });
  const queuePrompts = useMemo(() => planPrompts.map((p) => p.prompt), [planPrompts]);

  const storageKey = `comfy-genprompts-${planId}`;
  const [persisted] = useState(() => loadPersisted(storageKey, GEN_DEFAULTS));
  const [providerId, setProviderId] = useState<string>(persisted.providerId);
  const [model, setModel] = useState(persisted.model);
  const [instructions, setInstructions] = useState(persisted.instructions);
  const [instruction, setInstruction] = useState("");
  const [batchSize, setBatchSize] = useState(persisted.batchSize);
  const [targetCount, setTargetCount] = useState(persisted.targetCount);
  const [temperature, setTemperature] = useState(persisted.temperature);
  const [useQueueContext, setUseQueueContext] = useState(true);
  const [resultText, setResultText] = useState("");
  const [batching, setBatching] = useState(false);
  const batchAbortRef = useRef<AbortController | null>(null);
  // Stop requested, server not yet confirmed. Cancellation is checked between LLM
  // calls, so this window is a whole batch wide — the button must say so.
  const [stopping, setStopping] = useState(false);

  // Deliberately a synchronous write, not useDebouncedPersist: with no debounce
  // there is no window in which an unmount could drop a write, and this modal is
  // closed by unmounting — a debounced write would be the fragile choice here, not
  // the safe one. See docs/dev/frontend-core.md.
  useEffect(() => {
    savePersisted(storageKey, { instructions, providerId, model, batchSize, temperature, targetCount });
  }, [storageKey, instructions, providerId, model, batchSize, temperature, targetCount]);

  // ── Attaching to the background job ────────────────────────────────────────
  // No useJobSSE here on purpose. useAllJobsSSE is mounted once in TopBar and
  // never unmounts, so jobStore already holds every event for every job; a
  // modal-scoped subscription would re-create exactly the modal↔job coupling
  // this feature exists to remove. Don't "fix" this by adding one.
  // The attached job is *derived*, never mirrored into state — jobStore is the
  // single source of truth, and a copy here would need clearing on every
  // terminal event (and would go stale whenever this modal is unmounted, which
  // is precisely what the job is meant to survive).
  const jobKey = `comfy-genprompts-job-${planId}`;
  const activeJobs = useJobStore((s) => s.activeJobs);
  const job = useMemo(() => {
    // Primary: any live job that has announced itself for this plan.
    for (const p of activeJobs.values()) {
      if (p.job_type === "comfy_prompts" && p.plan_id === planId && !TERMINAL.includes(p.status)) return p;
    }
    // A job we just started is not matchable by plan_id yet: the queue's own
    // pending/running events don't carry one, only the job's first emit does.
    const started = startedJobId ? activeJobs.get(startedJobId) : undefined;
    return started && !TERMINAL.includes(started.status) ? started : undefined;
  }, [activeJobs, planId, startedJobId]);
  const jobRunning = !!job;

  // The server confirmed the stop (or the job ended on its own) — clear the flag
  // so a second run's Stop button is live again.
  useEffect(() => { if (!jobRunning) setStopping(false); }, [jobRunning]);

  // Read at render time, NOT inside the recovery effect: effects run in
  // declaration order, so the persist effect below would have already
  // overwritten the key with null before recovery could read it.
  const [savedJobId] = useState(() => loadPersisted(jobKey, { jobId: null as string | null }).jobId);

  // Persist the live job id so a hard reload — which empties jobStore — can
  // still find it. Cleared automatically once the job goes terminal.
  useEffect(() => { savePersisted(jobKey, { jobId: job?.job_id ?? null }); }, [jobKey, job]);

  // Reload recovery, once per mount: a persisted id with no jobStore entry. It
  // either finished while we were away (or was TTL-evicted), or it is still
  // running and simply hasn't emitted since the page reloaded — seeding the
  // store makes the bar appear now, and the global SSE stream takes over at the
  // job's next batch event.
  const recoveredRef = useRef(false);
  useEffect(() => {
    if (recoveredRef.current) return;
    recoveredRef.current = true;
    const savedId = savedJobId;
    if (!savedId || useJobStore.getState().activeJobs.has(savedId)) return;
    let dropped = false;
    jobsApi.get(savedId)
      .then((j) => {
        if (dropped) return;
        if (!TERMINAL.includes(j.status)) {
          useJobStore.getState().updateJob(j.id, {
            type: "progress", job_id: j.id, job_type: j.job_type, label: j.label,
            status: j.status, done: j.done_items, total: j.total_items,
            percent: 0, plan_id: planId,
          });
          return;
        }
        savePersisted(jobKey, { jobId: null });
        // mark_interrupted_jobs fails anything a restart left running — but the
        // rows it committed per batch really are in the queue, so say so rather
        // than letting it read as a total loss.
        const created = Number(j.result_data?.created ?? 0);
        if (j.status === "failed" && created > 0) {
          toast(`The last prompt job ended early (${j.error_msg ?? "unknown reason"}) — ` +
            `${created} prompt${created !== 1 ? "s" : ""} were kept`, { icon: "⚠️", duration: 7000 });
        }
      })
      .catch(() => { /* job row gone — nothing to re-attach to */ });
    return () => { dropped = true; };
  }, [jobKey, planId, savedJobId]);

  const provider = providers.find((p) => p.id === providerId) ?? providers[0];
  const effectiveProviderId = provider?.id ?? "";
  const lines = useMemo(() => resultText.split("\n").map((l) => l.trim()).filter(Boolean), [resultText]);

  function apiError(err: unknown): string {
    return (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      ?? "Prompt generation failed";
  }

  async function generateBatch(currentLines: string[]): Promise<string[]> {
    const existing = [...(useQueueContext ? queuePrompts : []), ...currentLines];
    const res = await comfyApi.generatePrompts({
      provider_id: effectiveProviderId,
      model_name: model,
      system_instructions: instructions,
      instruction,
      batch_size: batchSize,
      existing,
      temperature,
    }, batchAbortRef.current?.signal);
    return res.prompts;
  }

  /** Drop incoming prompts that (case-insensitively) already appear in `have`, so a
   *  repetitive model doesn't inflate the count or add duplicate rows. */
  function dedupeAgainst(have: string[], incoming: string[]): string[] {
    const seen = new Set(have.map((l) => l.trim().toLowerCase()));
    const out: string[] = [];
    for (const p of incoming) {
      const key = p.trim().toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(p);
    }
    return out;
  }

  async function handleBatch() {
    if (!effectiveProviderId || !instruction.trim()) return;
    batchAbortRef.current = new AbortController();
    setBatching(true);
    try {
      const prompts = dedupeAgainst(lines, await generateBatch(lines));
      if (prompts.length === 0) toast.error("The model returned no new prompts — try rephrasing the instruction");
      setResultText((prev) => (prev.trim() ? prev.replace(/\n$/, "") + "\n" : "") + prompts.join("\n"));
    } catch (err) {
      // An abort is the user closing the modal, not a failure to report.
      if (!axios.isCancel(err)) toast.error(apiError(err));
    } finally {
      batchAbortRef.current = null;
      setBatching(false);
    }
  }

  /** Close, abandoning an in-flight sync batch. That call can run for the
   *  provider's full 120 s timeout, and holding the modal hostage for it is worse
   *  than losing the batch — the user asked to leave. Aborting at least frees the
   *  connection instead of waiting on a result nothing will read. "Generate
   *  until N" is unaffected: it is a job, and outliving this modal is its point. */
  function handleClose() {
    batchAbortRef.current?.abort();
    onClose();
  }

  /** Hand the loop to the backend. Everything it needs is derived server-side
   *  from the plan — including the diverge-from context, hence no `existing`. */
  function handleStartJob() {
    if (!effectiveProviderId || !instruction.trim()) return;
    onStartJob({
      provider_id: effectiveProviderId,
      model_name: model,
      system_instructions: instructions,
      instruction,
      batch_size: batchSize,
      temperature,
      target_count: targetCount,
      use_existing_context: useQueueContext,
    });
  }

  /** Request the stop and wait for the server to confirm it. Deliberately NOT the
   *  optimistic flip TopBar uses for its own cancel button: cancellation here is
   *  cooperative and only checked between LLM calls, so at Stop time a paid batch
   *  is usually still in flight and will be committed. Flipping jobStore to
   *  "cancelled" fired TopBar's terminal toast immediately with the count as of
   *  that instant — "0 prompts kept" — and then `promptTerminalRef` suppressed the
   *  real terminal event, so the rows that did land were never accounted for.
   *  The server's own terminal event carries the true count; let it do the talking. */
  function handleStopJob() {
    if (!job) return;
    setStopping(true);
    jobsApi.cancel(job.job_id);
  }

  // Only the synchronous batch call blocks generating — closing is always allowed
  // (see handleClose); the whole point of the job path is that closing costs nothing.
  const generating = batching;
  // The job seeds its dedupe set once at the start, so rows added mid-run would
  // be invisible to it and get duplicated. Not cosmetic: keep these disabled.
  const jobBusy = jobRunning || startingJob;
  // promptsLoading joins it because every action below depends on the queue's
  // existing prompts — both as the diverge-from context and as the target gate
  // the server re-validates. Acting on an empty count starts a run the server
  // then rejects.
  const jobBlocks = jobBusy || promptsLoading;

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={handleClose}
    >
      <div className="panel" style={{ width: 640, maxWidth: "94vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="panel-h">
          <h3>Generate prompts</h3>
          <div style={{ flex: 1 }} />
          <button
            className="icon-btn"
            title={generating ? "Close — the batch being generated will be discarded" : "Close"}
            onClick={handleClose}
          >×</button>
        </div>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {providers.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
              No LLM providers configured — add one in Settings → LLM Providers first.
            </p>
          ) : (
            <>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <label style={{ fontSize: 12, color: "var(--fg-mute)" }}>Provider</label>
                <select
                  className="select" style={{ fontSize: 12 }}
                  value={effectiveProviderId}
                  onChange={(e) => { setProviderId(e.target.value); setModel(""); }}
                >
                  {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <div style={{ flex: 1, minWidth: 200 }}>
                  <ModelPicker
                    value={model}
                    onChange={setModel}
                    providerId={effectiveProviderId}
                    baseUrl={provider?.base_url}
                    placeholder={provider?.default_model || "model"}
                  />
                </div>
              </div>

              <div>
                <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "block", marginBottom: 4 }}>
                  Instructions — <b>how</b> prompts must be written (style, format, rules; saved per plan)
                </label>
                <textarea
                  className="input"
                  style={{ width: "100%", minHeight: 54, fontSize: 12, resize: "vertical" }}
                  placeholder={"e.g. danbooru-style tag prompts, always start with \"masterpiece, best quality\", max 40 tags, no artist names…"}
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                />
              </div>

              <div>
                <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "block", marginBottom: 4 }}>
                  Request — <b>what</b> to generate now
                </label>
                <textarea
                  className="input" autoFocus
                  style={{ width: "100%", minHeight: 44, fontSize: 12, resize: "vertical" }}
                  placeholder={"e.g. 1girl in varied fantasy settings, western comic style"}
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                />
              </div>

              <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap", fontSize: 12, color: "var(--fg-mute)" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  Batch
                  <input
                    className="input" type="number" min={1} max={10} style={{ width: 52, fontSize: 12 }}
                    value={batchSize} onChange={(e) => setBatchSize(Math.max(1, Math.min(10, Number(e.target.value) || 5)))}
                  />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 5 }}
                  title="Higher = more varied, less coherent. Some models ignore this.">
                  Temperature
                  <input
                    type="range" min={0} max={1.5} step={0.05} value={temperature}
                    onChange={(e) => setTemperature(Number(e.target.value))} style={{ width: 90 }}
                  />
                  <span className="mono">{temperature.toFixed(2)}</span>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}
                  title="Send the queue's existing prompts as context so new ones match their style but differ in content">
                  <input
                    type="checkbox" className="checkbox" checked={useQueueContext}
                    onChange={(e) => setUseQueueContext(e.target.checked)}
                  />
                  diverge from existing rows ({queuePrompts.length})
                </label>
              </div>

              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <button
                  className="btn primary sm"
                  disabled={generating || jobBlocks || !instruction.trim()}
                  onClick={handleBatch}
                  title={jobBusy ? "Wait for the running prompt job to finish" : "One batch into the review box below"}
                >
                  {batching ? "Generating…" : `Generate ${batchSize} more`}
                </button>
                <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>or</span>
                {jobRunning ? (
                  <button
                    className="btn sm"
                    style={{ color: "var(--bad)" }}
                    onClick={handleStopJob}
                    disabled={stopping}
                    title="Stops after the batch now in flight — its prompts are paid for, so they are kept"
                  >
                    {stopping ? "Stopping…" : "■ Stop"}
                  </button>
                ) : (
                  <button
                    className="btn ghost sm"
                    disabled={generating || jobBlocks || !instruction.trim() || queuePrompts.length >= targetCount}
                    onClick={handleStartJob}
                    title={
                      queuePrompts.length >= targetCount
                        ? `The queue already holds ${queuePrompts.length} prompts — raise the target`
                        : "Runs in the background, adding rows as they arrive — safe to close this window"
                    }
                  >
                    {startingJob ? "Starting…" : "Generate until"}
                  </button>
                )}
                <input
                  className="input" type="number" min={1} max={200} style={{ width: 60, fontSize: 12 }}
                  value={targetCount}
                  onChange={(e) => setTargetCount(Math.max(1, Math.min(200, Number(e.target.value) || 20)))}
                  disabled={jobBlocks}
                  title="Total prompts the queue should hold when the job finishes"
                />
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>
                  {queuePrompts.length} in queue
                </span>
              </div>

              {jobRunning && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <JobProgressBar
                    message={job?.message ?? "Generating prompts…"}
                    percent={Math.max(0, job?.percent ?? 0)}
                  />
                  <span style={{ fontSize: 11, color: "var(--fg-dim)" }}>
                    {stopping
                      ? "Stopping after the batch in flight — its prompts are already paid for, so they are kept."
                      : "Rows are added as each batch arrives — you can close this window and keep working."}
                  </span>
                </div>
              )}

              <div>
                <label style={{ fontSize: 11, color: "var(--fg-dim)", display: "block", marginBottom: 4 }}>
                  One prompt per line — edit or delete before adding; remaining lines steer the next batch away from themselves.
                  <b> Generate until</b> skips this box and writes rows directly.
                </label>
                <textarea
                  className="input mono"
                  style={{ width: "100%", minHeight: 160, fontSize: 11.5, resize: "vertical" }}
                  placeholder="Generated prompts appear here…"
                  value={resultText}
                  onChange={(e) => setResultText(e.target.value)}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button className="btn ghost" onClick={handleClose}>Cancel</button>
                <button
                  className="btn primary"
                  disabled={generating || jobBlocks || adding || lines.length === 0}
                  onClick={() => onAdd(lines)}
                  title={jobBusy ? "Wait for the running prompt job to finish" : undefined}
                >
                  {adding ? "Adding…" : `Add ${lines.length} row${lines.length !== 1 ? "s" : ""}`}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
