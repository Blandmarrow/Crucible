import { useState } from "react";
import { coerceCellValue } from "../../api/comfy";
import type { ComfyPlan, PinnedParam } from "../../api/comfy";

interface Props {
  plan: ComfyPlan;
  disabled: boolean;
  /** Persist the full pinned_params list (PATCH the plan). */
  onSavePins: (pins: PinnedParam[]) => void;
}

function templateValue(plan: ComfyPlan, pin: PinnedParam): unknown {
  return plan.workflow_json[pin.node_id]?.inputs?.[pin.input];
}

function displayValue(plan: ComfyPlan, pin: PinnedParam): string {
  if (pin.int_mode === "random") return "random per row";
  if (pin.int_mode === "increment") return "+1 increment per row";
  if (pin.value !== null && pin.value !== undefined && pin.value !== "") return String(pin.value);
  const tv = templateValue(plan, pin);
  return tv === undefined ? "—" : `template (${String(tv)})`;
}

/** Chips for run-default pins (per_row=false): visible and editable right above the queue. */
export default function ComfyDefaultsStrip({ plan, disabled, onSavePins }: Props) {
  const [editing, setEditing] = useState<PinnedParam | null>(null);
  const [draft, setDraft] = useState("");

  const defaults = plan.pinned_params.filter((p) => !p.per_row);
  if (defaults.length === 0) return null;

  function save(clear: boolean) {
    if (!editing) return;
    const tv = templateValue(plan, editing);
    const value: PinnedParam["value"] =
      clear || draft.trim() === "" ? null : coerceCellValue(draft, typeof tv === "number");
    onSavePins(plan.pinned_params.map((p) =>
      p.node_id === editing.node_id && p.input === editing.input ? { ...p, value } : p
    ));
    setEditing(null);
  }

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
      padding: "8px 12px", marginBottom: 8,
      border: "1px solid var(--line)", borderRadius: "var(--r)", background: "var(--surface-1)",
    }}>
      <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: "var(--fg-dim)" }}
        title="Pinned run defaults — applied to every row of a run. Give a parameter its own column with the “per row” switch in Workflow & Pins.">
        Run defaults
      </span>
      {defaults.map((p) => (
        <button
          key={`${p.node_id}::${p.input}`}
          className="btn ghost sm"
          style={{ fontSize: 11, padding: "1px 8px", display: "inline-flex", gap: 6, alignItems: "center", maxWidth: 340 }}
          disabled={disabled}
          title={`#${p.node_id} ${plan.workflow_json[p.node_id]?.class_type ?? "?"} · ${p.input} — click to edit`}
          onClick={() => { setEditing(p); setDraft(p.value === null || p.value === undefined ? "" : String(p.value)); }}
        >
          <span className="mono" style={{ color: "var(--fg-dim)", fontSize: 10 }}>#{p.node_id} · {p.alias}</span>
          <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {displayValue(plan, p)}
          </span>
          <span style={{ color: "var(--accent, var(--fg-mute))" }}>✎</span>
        </button>
      ))}

      {editing && (
        <div
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setEditing(null)}
        >
          <div className="panel" style={{ width: 440, maxWidth: "92vw" }} onClick={(e) => e.stopPropagation()}>
            <div className="panel-h"><h3>Run default: {editing.alias}</h3></div>
            <div className="panel-b">
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 8px" }}>
                <span className="mono">#{editing.node_id} {plan.workflow_json[editing.node_id]?.class_type ?? "?"} · {editing.input}</span>
                {" — "}applies to every row of a run. Blank = template value{" "}
                (<span className="mono">{String(templateValue(plan, editing) ?? "—")}</span>).
              </p>
              <input
                className="input" autoFocus
                type={typeof templateValue(plan, editing) === "number" ? "number" : "text"}
                style={{ width: "100%", fontSize: 12 }}
                placeholder={`template: ${String(templateValue(plan, editing) ?? "")}`}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") save(false);
                  if (e.key === "Escape") setEditing(null);
                }}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
                <button className="btn ghost" onClick={() => setEditing(null)}>Cancel</button>
                <button className="btn ghost" onClick={() => save(true)}>Use template</button>
                <button className="btn primary" onClick={() => save(false)}>Save</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
