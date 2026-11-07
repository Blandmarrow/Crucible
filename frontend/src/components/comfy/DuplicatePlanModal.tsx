import { useState } from "react";
import ModalShell from "../common/ModalShell";
import type { ComfyPlanSummary } from "../../api/comfy";

interface Props {
  plan: ComfyPlanSummary;
  /** Every plan name in the dataset, to propose a free one. */
  takenNames: string[];
  isPending: boolean;
  onConfirm: (body: { name: string; include_rows: boolean }) => void;
  onClose: () => void;
}

function freeName(base: string, taken: Set<string>): string {
  const first = `${base} (copy)`;
  if (!taken.has(first)) return first;
  for (let n = 2; ; n++) {
    const next = `${base} (copy ${n})`;
    if (!taken.has(next)) return next;
  }
}

/** Duplicate a plan: its workflow, pins, run defaults, folders and blueprint, and
 *  optionally its rows, reset to pending. Runs and images are never copied. */
export default function DuplicatePlanModal({ plan, takenNames, isPending, onConfirm, onClose }: Props) {
  const taken = new Set(takenNames);
  const [name, setName] = useState(() => freeName(plan.name, taken));
  const [includeRows, setIncludeRows] = useState(true);
  const trimmed = name.trim();
  const clash = taken.has(trimmed);
  const canSubmit = !!trimmed && !clash && !isPending;

  return (
    <ModalShell label="Duplicate plan" onClose={onClose} closeOnBackdrop>
      <h3>Duplicate plan</h3>
      <p>
        Copies the workflow, the pins, the folders and the blueprint of <b>{plan.name}</b>.
        Runs and images are not copied.
      </p>
      <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", flexDirection: "column", gap: 4 }}>
        Name
        <input
          className="input" autoFocus value={name} maxLength={200}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && canSubmit) onConfirm({ name: trimmed, include_rows: includeRows }); }}
        />
      </label>
      {clash && <div style={{ fontSize: 12, color: "var(--bad)", marginTop: 6 }}>A plan with that name exists.</div>}
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, marginTop: 12 }}>
        <input type="checkbox" className="checkbox" checked={includeRows} onChange={(e) => setIncludeRows(e.target.checked)} />
        Include its {plan.row_count} row{plan.row_count !== 1 ? "s" : ""}, reset to pending
      </label>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!canSubmit} onClick={() => onConfirm({ name: trimmed, include_rows: includeRows })}>
          {isPending ? "Duplicating…" : "Duplicate"}
        </button>
      </div>
    </ModalShell>
  );
}
