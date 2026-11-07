import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi } from "../../api/comfy";
import { providersApi } from "../../api/providers";
import { useJobStore } from "../../store/jobStore";
import { apiErrorDetail } from "../../utils/apiError";
import ModelPicker from "../providers/ModelPicker";
import JobProgressBar from "../common/JobProgressBar";
import ModalShell from "../common/ModalShell";
import NumberField from "../common/NumberField";

const TERMINAL = ["completed", "failed", "cancelled"];
const clampBatch = (n: number) => Math.min(10, Math.max(1, Math.round(n)));

interface Props {
  planId: string;
  onClose: () => void;
}

/** Rewrites the plan's template-composed prompts into prose with an LLM. The work runs as
    a `comfy_rewrite` background job; closing this modal never cancels it, and TopBar
    refreshes the rows and toasts the outcome. The attached job is derived from jobStore,
    as in OutlineModal. The estimate is the budget the user reviews: submit sends it back
    as `confirm_budget`, and the server 409s if it moved. */
export default function RewriteModal({ planId, onClose }: Props) {
  const qc = useQueryClient();
  const { data: providers = [] } = useQuery({ queryKey: ["providers"], queryFn: providersApi.list });
  // Read for defaults only: what the user picks here is not written back.
  const { data: gen } = useQuery({
    queryKey: ["comfy", "gen-settings-defaults", planId],
    queryFn: () => comfyApi.getGenSettings(planId),
    staleTime: 0,
  });
  const [pickedProvider, setPickedProvider] = useState<string | null>(null);
  const [pickedModel, setPickedModel] = useState<string | null>(null);
  const [pickedBatch, setPickedBatch] = useState<number | null>(null);
  const [temperature, setTemperature] = useState(0.7);
  const [starting, setStarting] = useState(false);
  const [startedJobId, setStartedJobId] = useState<string | null>(null);

  const providerId = pickedProvider ?? (gen?.provider_id || providers[0]?.id || "");
  const provider = providers.find((p) => p.id === providerId) ?? providers[0];
  const effectiveProviderId = provider?.id ?? "";
  const model = pickedModel ?? (pickedProvider === null ? gen?.model ?? "" : "");
  const batchSize = pickedBatch ?? clampBatch(gen?.batch_size ?? 5);

  const { data: estimate, isError, error } = useQuery({
    queryKey: ["comfy", "blueprint-rewrite", planId, batchSize],
    queryFn: ({ signal }) => comfyApi.rewriteEstimate(planId, batchSize, signal),
    staleTime: 0,
  });

  const activeJobs = useJobStore((s) => s.activeJobs);
  const job = useMemo(() => {
    for (const p of activeJobs.values()) {
      if (p.job_type === "comfy_rewrite" && p.plan_id === planId && !TERMINAL.includes(p.status)) return p;
    }
    const started = startedJobId ? activeJobs.get(startedJobId) : undefined;
    return started && !TERMINAL.includes(started.status) ? started : undefined;
  }, [activeJobs, planId, startedJobId]);
  const rewriting = !!job || starting;
  const nothing = !!estimate && estimate.rows === 0;

  async function handleRewrite() {
    if (!effectiveProviderId || !estimate || estimate.rows === 0) return;
    setStarting(true);
    try {
      const res = await comfyApi.rewriteBlueprint(planId, {
        provider_id: effectiveProviderId,
        model_name: model,
        batch_size: batchSize,
        temperature,
        confirm_budget: estimate.budget,
      });
      setStartedJobId(res.job_id);
      // See OutlineModal: the queue's pending event carries no plan_id, and jobStore
      // merges by id, so seeding it keeps a reopened modal from offering a doomed 409.
      useJobStore.getState().updateJob(res.job_id, {
        type: "progress", job_id: res.job_id, job_type: "comfy_rewrite",
        status: "pending", done: 0, total: res.total, percent: 0, plan_id: planId,
      });
      onClose();
    } catch (err) {
      toast.error(apiErrorDetail(err, "Could not start the rewrite"));
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        qc.invalidateQueries({ queryKey: ["comfy", "blueprint-rewrite", planId] });
      }
    } finally {
      setStarting(false);
    }
  }

  return (
    <ModalShell label="Rewrite prompts" onClose={onClose} closeOnBackdrop style={{ width: 600, maxWidth: "94vw" }}>
      <div className="panel-h">
        <h3>Rewrite prompts into prose</h3>
        <div style={{ flex: 1 }} />
        <button className="icon-btn" title="Close — a running rewrite keeps going" onClick={onClose}>×</button>
      </div>
      <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }} data-testid="rewrite-modal">
        {providers.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            No LLM providers configured — add one in Settings → LLM Providers first.
          </p>
        ) : (
          <>
            <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
              An LLM rewrites the pending and failed template prompts of scenarios that opted in.
              Rewritten prompts replace the template text on those rows.
            </p>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <label style={{ fontSize: 12, color: "var(--fg-mute)" }}>Provider</label>
              <select
                className="select" style={{ fontSize: 12 }} data-testid="rewrite-provider"
                value={effectiveProviderId} disabled={rewriting}
                onChange={(e) => { setPickedProvider(e.target.value); setPickedModel(""); }}
              >
                {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <div style={{ flex: 1, minWidth: 200 }}>
                <ModelPicker
                  value={model}
                  onChange={setPickedModel}
                  providerId={effectiveProviderId}
                  baseUrl={provider?.base_url}
                  placeholder={provider?.default_model || "model"}
                />
              </div>
            </div>
            <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--fg-mute)" }}
                title="Prompts rewritten per LLM call (1 to 10).">
                Batch size
                <NumberField className="input" style={{ width: 60, height: 30, fontSize: 12 }}
                  aria-label="Batch size" data-testid="rewrite-batch" min={1} max={10}
                  value={batchSize} clamp={clampBatch} disabled={rewriting}
                  onCommit={(n) => setPickedBatch(n)} />
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--fg-mute)" }}
                title="Higher = more varied, less coherent. Some models ignore this.">
                Temperature
                <input type="range" min={0} max={2} step={0.05} value={temperature} disabled={rewriting}
                  onChange={(e) => setTemperature(Number(e.target.value))} style={{ width: 90 }} />
                <span className="mono">{temperature.toFixed(2)}</span>
              </label>
            </div>
            <div style={{ fontSize: 12 }} data-testid="rewrite-estimate">
              {isError && !estimate ? (
                <span style={{ color: "var(--bad)" }}>{apiErrorDetail(error, "Could not work out the estimate.")}</span>
              ) : !estimate ? (
                <span style={{ color: "var(--fg-mute)" }}>Working it out…</span>
              ) : nothing ? (
                <span style={{ color: "var(--fg-mute)" }}>Nothing to rewrite: no pending or failed template prompts.</span>
              ) : (
                <span>
                  {estimate.rows} prompt{estimate.rows !== 1 ? "s" : ""} in {estimate.leaves} folder{estimate.leaves !== 1 ? "s" : ""} · up to {estimate.budget} LLM call{estimate.budget !== 1 ? "s" : ""}
                  <span style={{ color: "var(--fg-mute)" }}> (about {estimate.calls} expected)</span>
                </span>
              )}
            </div>
            {rewriting && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <JobProgressBar message={job?.message || "Rewriting…"} percent={Math.max(0, job?.percent ?? 0)} />
                <span style={{ fontSize: 11, color: "var(--fg-dim)" }}>
                  You can close this window. A toast says when the rewrite is done.
                </span>
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="btn ghost" onClick={onClose}>Close</button>
              <button className="btn primary" data-testid="rewrite-start"
                disabled={rewriting || !effectiveProviderId || !estimate || nothing} onClick={handleRewrite}>
                {rewriting ? "Rewriting…" : "Rewrite"}
              </button>
            </div>
          </>
        )}
      </div>
    </ModalShell>
  );
}
