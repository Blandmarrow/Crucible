import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { comfyApi } from "../../api/comfy";
import { providersApi } from "../../api/providers";
import ModelPicker from "../providers/ModelPicker";
import { loadPersisted, savePersisted } from "../../utils/persistentState";

interface Props {
  planId: string;
  /** Existing queue prompts — sent as anti-similarity context alongside prior batches. */
  queuePrompts: string[];
  onAdd: (prompts: string[]) => void;
  adding: boolean;
  onClose: () => void;
}

/** Standing settings worth keeping between sessions, per plan. */
interface PersistedGenSettings {
  instructions: string;
  providerId: string;
  model: string;
  batchSize: number;
  temperature: number;
}

const GEN_DEFAULTS: PersistedGenSettings = {
  instructions: "", providerId: "", model: "", batchSize: 5, temperature: 0.9,
};

/** LLM prompt generation: small batches, each pushed to diverge from everything
    already generated/queued. "Generate until N" loops the same batch call. */
export default function GeneratePromptsModal({ planId, queuePrompts, onAdd, adding, onClose }: Props) {
  const { data: providers = [] } = useQuery({ queryKey: ["providers"], queryFn: providersApi.list });

  const storageKey = `comfy-genprompts-${planId}`;
  const [persisted] = useState(() => loadPersisted(storageKey, GEN_DEFAULTS));
  const [providerId, setProviderId] = useState<string>(persisted.providerId);
  const [model, setModel] = useState(persisted.model);
  const [instructions, setInstructions] = useState(persisted.instructions);
  const [instruction, setInstruction] = useState("");
  const [batchSize, setBatchSize] = useState(persisted.batchSize);
  const [untilN, setUntilN] = useState(20);
  const [temperature, setTemperature] = useState(persisted.temperature);
  const [useQueueContext, setUseQueueContext] = useState(true);
  const [resultText, setResultText] = useState("");
  const [busy, setBusy] = useState<"batch" | "until" | null>(null);
  const stopRef = useRef(false);

  // Deliberately a synchronous write, not useDebouncedPersist: with no debounce
  // there is no window in which an unmount could drop a write, and this modal is
  // closed by unmounting — a debounced write would be the fragile choice here, not
  // the safe one. See docs/dev/frontend-core.md.
  useEffect(() => {
    savePersisted(storageKey, { instructions, providerId, model, batchSize, temperature });
  }, [storageKey, instructions, providerId, model, batchSize, temperature]);

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
    });
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
    setBusy("batch");
    try {
      const prompts = dedupeAgainst(lines, await generateBatch(lines));
      if (prompts.length === 0) toast.error("The model returned no new prompts — try rephrasing the instruction");
      setResultText((prev) => (prev.trim() ? prev.replace(/\n$/, "") + "\n" : "") + prompts.join("\n"));
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleUntil() {
    if (!effectiveProviderId || !instruction.trim()) return;
    setBusy("until");
    stopRef.current = false;
    let current = [...lines];
    let text = resultText;
    // Each iteration is a paid LLM call and duplicates are dropped client-side, so a
    // model trickling near-duplicates must not loop unbounded. 3× the ideal call count
    // tolerates heavy duplicate wastage while still stopping a stuck model.
    const maxCalls = Math.max(12, Math.ceil((untilN - current.length) / batchSize) * 3);
    let call = 0;
    try {
      for (; current.length < untilN && call < maxCalls && !stopRef.current; call++) {
        const prompts = dedupeAgainst(current, await generateBatch(current));
        if (prompts.length === 0) {
          toast.error("The model returned no new prompts — stopping");
          break;
        }
        current = [...current, ...prompts];
        text = (text.trim() ? text.replace(/\n$/, "") + "\n" : "") + prompts.join("\n");
        setResultText(text);
      }
      // Reached the call cap without hitting the target and without a user stop or
      // empty batch — tell the user rather than stopping silently short of N.
      if (current.length < untilN && call >= maxCalls && !stopRef.current) {
        toast(`Stopped at ${current.length} prompts (call limit) — click "Generate until" again to continue`);
      }
    } catch (err) {
      toast.error(apiError(err));
    } finally {
      setBusy(null);
      stopRef.current = false;
    }
  }

  const generating = busy !== null;

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={() => { if (!generating) onClose(); }}
    >
      <div className="panel" style={{ width: 640, maxWidth: "94vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="panel-h">
          <h3>Generate prompts</h3>
          <div style={{ flex: 1 }} />
          <button className="icon-btn" title="Close" onClick={onClose} disabled={generating}>×</button>
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
                  disabled={generating || !instruction.trim()}
                  onClick={handleBatch}
                >
                  {busy === "batch" ? "Generating…" : `Generate ${batchSize} more`}
                </button>
                <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>or</span>
                {busy === "until" ? (
                  <button className="btn sm" style={{ color: "var(--bad)" }} onClick={() => { stopRef.current = true; }}>
                    ■ Stop (at {lines.length})
                  </button>
                ) : (
                  <button
                    className="btn ghost sm"
                    disabled={generating || !instruction.trim() || lines.length >= untilN}
                    onClick={handleUntil}
                  >
                    Generate until
                  </button>
                )}
                <input
                  className="input" type="number" min={1} max={200} style={{ width: 60, fontSize: 12 }}
                  value={untilN} onChange={(e) => setUntilN(Math.max(1, Number(e.target.value) || 20))}
                  disabled={generating}
                />
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>{lines.length} prompt{lines.length !== 1 ? "s" : ""}</span>
              </div>

              <div>
                <label style={{ fontSize: 11, color: "var(--fg-dim)", display: "block", marginBottom: 4 }}>
                  One prompt per line — edit or delete before adding; remaining lines steer the next batch away from themselves.
                </label>
                <textarea
                  className="input mono"
                  style={{ width: "100%", minHeight: 160, fontSize: 11.5, resize: "vertical" }}
                  placeholder="Generated prompts appear here…"
                  value={resultText}
                  onChange={(e) => setResultText(e.target.value)}
                  disabled={busy === "until"}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button className="btn ghost" onClick={onClose} disabled={generating}>Cancel</button>
                <button
                  className="btn primary"
                  disabled={generating || adding || lines.length === 0}
                  onClick={() => onAdd(lines)}
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
