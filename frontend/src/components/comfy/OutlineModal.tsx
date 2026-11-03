import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi } from "../../api/comfy";
import { providersApi } from "../../api/providers";
import { useJobStore } from "../../store/jobStore";
import { apiErrorDetail } from "../../utils/apiError";
import ModelPicker from "../providers/ModelPicker";
import JobProgressBar from "../common/JobProgressBar";
import ModalShell from "../common/ModalShell";

const TERMINAL = ["completed", "failed", "cancelled"];

interface Props {
  planId: string;
  /** The last brief used, to prefill the box. */
  initialBrief: string | null;
  onClose: () => void;
}

/** Asks an LLM to draft a blueprint from a description. The call runs as a `comfy_outline`
    background job and saves a draft on the blueprint; it never changes the spec. Closing
    this modal never cancels it. The attached job is derived from jobStore, not subscribed
    to here (docs/dev/comfy-prompts.md § Frontend). */
export default function OutlineModal({ planId, initialBrief, onClose }: Props) {
  const { data: providers = [] } = useQuery({ queryKey: ["providers"], queryFn: providersApi.list });
  // Read for defaults only: what the user picks here is not written back.
  const { data: gen } = useQuery({
    queryKey: ["comfy", "gen-settings-defaults", planId],
    queryFn: () => comfyApi.getGenSettings(planId),
    staleTime: 0,
  });
  const [brief, setBrief] = useState(initialBrief ?? "");
  const [pickedProvider, setPickedProvider] = useState<string | null>(null);
  const [pickedModel, setPickedModel] = useState<string | null>(null);
  const [temperature, setTemperature] = useState(0.7);
  const [starting, setStarting] = useState(false);
  const [startedJobId, setStartedJobId] = useState<string | null>(null);

  const providerId = pickedProvider ?? (gen?.provider_id || providers[0]?.id || "");
  const provider = providers.find((p) => p.id === providerId) ?? providers[0];
  const effectiveProviderId = provider?.id ?? "";
  const model = pickedModel ?? (pickedProvider === null ? gen?.model ?? "" : "");

  const activeJobs = useJobStore((s) => s.activeJobs);
  const job = useMemo(() => {
    for (const p of activeJobs.values()) {
      if (p.job_type === "comfy_outline" && p.plan_id === planId && !TERMINAL.includes(p.status)) return p;
    }
    const started = startedJobId ? activeJobs.get(startedJobId) : undefined;
    return started && !TERMINAL.includes(started.status) ? started : undefined;
  }, [activeJobs, planId, startedJobId]);
  const drafting = !!job || starting;

  async function handleDraft() {
    if (!effectiveProviderId || !brief.trim()) return;
    setStarting(true);
    try {
      const res = await comfyApi.outlineBlueprint(planId, {
        provider_id: effectiveProviderId,
        model_name: model || undefined,
        brief,
        temperature,
      });
      setStartedJobId(res.job_id);
      // The queue's own pending event carries no plan_id; until _run's first emit, a
      // job queued behind a long run would be invisible to the plan_id scan above, and
      // a reopened modal would offer Draft again only to get a 409. jobStore merges by
      // id, so the field survives every later event.
      useJobStore.getState().updateJob(res.job_id, {
        type: "progress", job_id: res.job_id, job_type: "comfy_outline",
        status: "pending", done: 0, total: 1, percent: 0, plan_id: planId,
      });
    } catch (err) {
      toast.error(apiErrorDetail(err, "Could not start the draft"));
    } finally {
      setStarting(false);
    }
  }

  return (
    <ModalShell label="Draft a blueprint" onClose={onClose} closeOnBackdrop style={{ width: 640, maxWidth: "94vw" }}>
      <div className="panel-h">
        <h3>Draft from a description</h3>
        <div style={{ flex: 1 }} />
        <button className="icon-btn" title="Close — a running draft keeps going" onClick={onClose}>×</button>
      </div>
      <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }} data-testid="outline-modal">
        {providers.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            No LLM providers configured — add one in Settings → LLM Providers first.
          </p>
        ) : (
          <>
            <div>
              <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "block", marginBottom: 4 }}>
                Describe what you want. The draft is saved for review and nothing else changes.
              </label>
              <textarea
                className="input" autoFocus data-testid="outline-brief"
                style={{ width: "100%", minHeight: 90, fontSize: 12, resize: "vertical" }}
                placeholder="e.g. a medieval set, 6 characters, each in 4 biomes, alone and in pairs"
                value={brief} maxLength={20000} disabled={drafting}
                onChange={(e) => setBrief(e.target.value)}
              />
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <label style={{ fontSize: 12, color: "var(--fg-mute)" }}>Provider</label>
              <select
                className="select" style={{ fontSize: 12 }} data-testid="outline-provider"
                value={effectiveProviderId} disabled={drafting}
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
            <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--fg-mute)" }}
              title="Higher = more varied, less coherent. Some models ignore this.">
              Temperature
              <input type="range" min={0} max={1.5} step={0.05} value={temperature} disabled={drafting}
                onChange={(e) => setTemperature(Number(e.target.value))} style={{ width: 90 }} />
              <span className="mono">{temperature.toFixed(2)}</span>
            </label>
            {drafting && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <JobProgressBar message={job?.message || "Drafting…"} percent={Math.max(0, job?.percent ?? 0)} />
                <span style={{ fontSize: 11, color: "var(--fg-dim)" }}>
                  You can close this window. A toast says when the draft is ready.
                </span>
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="btn ghost" onClick={onClose}>Close</button>
              <button className="btn primary" data-testid="outline-draft"
                disabled={drafting || !effectiveProviderId || !brief.trim()} onClick={handleDraft}>
                {drafting ? "Drafting…" : "Draft"}
              </button>
            </div>
          </>
        )}
      </div>
    </ModalShell>
  );
}
