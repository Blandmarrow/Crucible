import { GATE_FLAGS } from "../../api/runs";
import type { ProviderOut } from "../../api/providers";
import { FLAG_OPTIONS } from "../../constants/flags";
import NumberField from "../common/NumberField";
import ModelPicker from "../providers/ModelPicker";
import type { GateForm, LoopForm, RewriteForm } from "./runLoopForms";

/**
 * The Runs page's M6 §5 sections: the goal loop's caps, the gate, and the rewrite
 * stage. Their forms and converters are in `runLoopForms.ts`.
 */

const clampInt = (lo: number, hi: number) => (n: number) => Math.min(hi, Math.max(lo, Math.round(n)));

export function RunLoopFields({ value, onChange, paidCap }: {
  value: LoopForm; onChange: (v: LoopForm) => void; paidCap: boolean;
}) {
  const set = (patch: Partial<LoopForm>) => onChange({ ...value, ...patch });
  return (
    <div className="form-row">
      <div className="lbl-col">
        <h4>Repeat</h4>
        <p>Run pass after pass until every folder holds its target of images that pass the gate. A folder with no gain in two passes is given up on.</p>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label className="row-flex" style={{ gap: 8 }}>
          <input type="checkbox" className="checkbox" checked={value.on} onChange={(e) => set({ on: e.target.checked })} />
          <span style={{ fontSize: 12.5 }}>Repeat until every folder is full</span>
        </label>
        {value.on && (
          <div style={{ display: "grid", gridTemplateColumns: "auto 110px", gap: "6px 12px", alignItems: "center", maxWidth: 360 }}>
            <span style={{ fontSize: 12.5 }}>Most passes</span>
            <NumberField className="input" value={value.maxPasses} clamp={clampInt(1, 50)} onCommit={(n) => set({ maxPasses: n })} aria-label="Most passes" />
            <span style={{ fontSize: 12.5 }}>Most images</span>
            <NumberField className="input" value={value.maxImages} clamp={clampInt(1, 100000)} onCommit={(n) => set({ maxImages: n })} aria-label="Most images" />
            <span style={{ fontSize: 12.5 }}>Most minutes of work</span>
            <NumberField className="input" value={value.maxMinutes} clamp={clampInt(1, 10080)} onCommit={(n) => set({ maxMinutes: n })} aria-label="Most minutes" />
            {paidCap && (
              <>
                <span style={{ fontSize: 12.5 }} title="Rewrite requests and captioned images sent to a billed provider">Most paid LLM calls</span>
                <NumberField className="input" value={value.maxPaidCalls} clamp={clampInt(1, 1000000)} onCommit={(n) => set({ maxPaidCalls: n })} aria-label="Most paid LLM calls" />
              </>
            )}
          </div>
        )}
        {value.on && (
          <span style={{ fontSize: 11.5, color: "var(--fg-mute)" }}>
            Minutes count only time spent working, not time paused.
            {paidCap && " A billed provider is in this run, so its calls are capped; when captioning is billed the cap must cover captioning every image."}
          </span>
        )}
      </div>
    </div>
  );
}

const GATE_OPTIONS = FLAG_OPTIONS.filter((o) => (GATE_FLAGS as readonly string[]).includes(o.key));

export function RunGateFields({ value, onChange, runAesthetic }: {
  value: GateForm; onChange: (v: GateForm) => void; runAesthetic: boolean;
}) {
  const set = (patch: Partial<GateForm>) => onChange({ ...value, ...patch });
  const toggle = (k: string) => set({ flags: value.flags.includes(k) ? value.flags.filter((f) => f !== k) : [...value.flags, k] });
  return (
    <div className="form-row">
      <div className="lbl-col">
        <h4>Gate</h4>
        <p>Images that fail move to <code>rejects/</code> under the base folder and stop counting toward their folder. Nothing is deleted.</p>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div className="row-flex" style={{ gap: 12, flexWrap: "wrap" }}>
          {GATE_OPTIONS.map((o) => (
            <label key={o.key} className="row-flex" style={{ gap: 6 }}>
              <input type="checkbox" className="checkbox" checked={value.flags.includes(o.key)} onChange={() => toggle(o.key)} />
              <span style={{ fontSize: 12.5 }}>Reject {o.label.toLowerCase()}</span>
            </label>
          ))}
        </div>
        <label className="row-flex" style={{ gap: 8, opacity: runAesthetic ? 1 : 0.5 }} title={runAesthetic ? undefined : "Switch on the aesthetic score first"}>
          <input type="checkbox" className="checkbox" checked={value.aestheticOn && runAesthetic} disabled={!runAesthetic}
            onChange={(e) => set({ aestheticOn: e.target.checked })} />
          <span style={{ fontSize: 12.5 }}>Reject an aesthetic score below</span>
          <NumberField className="input" style={{ width: 80 }} value={value.aestheticMin} disabled={!runAesthetic || !value.aestheticOn}
            clamp={(n) => Math.min(10, Math.max(0, n))} onCommit={(n) => set({ aestheticMin: n })} aria-label="Lowest aesthetic score" step={0.1} />
        </label>
        <span style={{ fontSize: 11.5, color: "var(--fg-mute)" }}>An image whose pixels changed since it was scored always fails.</span>
      </div>
    </div>
  );
}

export function RunRewriteFields({ value, onChange, providers, scenarios }: {
  value: RewriteForm; onChange: (v: RewriteForm) => void; providers: ProviderOut[]; scenarios: string[];
}) {
  const set = (patch: Partial<RewriteForm>) => onChange({ ...value, ...patch });
  const providerId = value.providerId || providers[0]?.id || "";
  const provider = providers.find((p) => p.id === providerId);
  const toggle = (s: string) => set({ scenarios: value.scenarios.includes(s) ? value.scenarios.filter((x) => x !== s) : [...value.scenarios, s] });
  return (
    <div className="form-row">
      <div className="lbl-col">
        <h4>Rewrite</h4>
        <p>Each pass rewrites its new template prompts into prose before generating. Ticking a scenario here is the review the rewrite needs; a folder whose rewrite fails sits that pass out.</p>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {scenarios.length === 0
          ? <span style={{ fontSize: 12.5, color: "var(--fg-mute)" }}>No scenario in the saved blueprint has “Rewrite into prose” on.</span>
          : (
            <div className="row-flex" style={{ gap: 12, flexWrap: "wrap" }}>
              {scenarios.map((s) => (
                <label key={s} className="row-flex" style={{ gap: 6 }}>
                  <input type="checkbox" className="checkbox" checked={value.scenarios.includes(s)} onChange={() => toggle(s)} />
                  <span style={{ fontSize: 12.5 }}>{s}</span>
                </label>
              ))}
            </div>
          )}
        <select className="select" value={providerId} onChange={(e) => set({ providerId: e.target.value, modelName: "" })} aria-label="Rewrite provider">
          {providers.length === 0 && <option value="">No LLM providers — add one in Settings</option>}
          {providers.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_billed ? " (billed)" : ""}</option>)}
        </select>
        {provider && (
          <ModelPicker value={value.modelName} onChange={(m) => set({ modelName: m })} providerId={provider.id}
            baseUrl={provider.base_url} placeholder={provider.default_model || "Model"} />
        )}
      </div>
    </div>
  );
}
