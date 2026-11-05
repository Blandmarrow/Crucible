import { useState } from "react";
import ModalShell from "../common/ModalShell";
import type { ComfyPlanSummary, ComfyRow, LinkedMode } from "../../api/comfy";
import { useCopyRowsMutation } from "./copyRows";

interface Props {
  /** The plan the rows are in. */
  sourcePlanId: string;
  datasetId: string;
  /** The dataset's plans; the source is left out of the choice. */
  plans: ComfyPlanSummary[];
  /** The selected rows, to know which the blueprint counts. */
  rows: ComfyRow[];
  /** Preset by a drop on a plan (M6 §8c stage 6), which opens this to ask about a move. */
  initialTargetId?: string;
  initialMove?: boolean;
  /** A move empties the source of these rows: the page clears its selection. */
  onMoved?: () => void;
  onClose: () => void;
}

/** Copy to plan (n)…: the button route for dropping rows on a plan in the rail. A copy
 *  keeps each row's folder and the values the target also has per row; a move takes them
 *  out of this plan, and asks first about rows the blueprint counts. */
export default function CopyRowsToPlanModal({
  sourcePlanId, datasetId, plans, rows, initialTargetId, initialMove = false, onMoved, onClose,
}: Props) {
  const targets = plans.filter((p) => p.id !== sourcePlanId);
  const [targetId, setTargetId] = useState(initialTargetId ?? targets[0]?.id ?? "");
  const [move, setMove] = useState(initialMove);
  const linkedCount = rows.filter((r) => r.blueprint_linked).length;
  const [asking, setAsking] = useState(initialMove && linkedCount > 0);
  const mutation = useCopyRowsMutation((v) => {
    if (v.move) onMoved?.();
    onClose();
  });
  const target = targets.find((p) => p.id === targetId);

  function send(linked: LinkedMode) {
    if (!target || mutation.isPending) return;
    mutation.mutate({
      sourcePlanId, sourceDatasetId: datasetId, targetPlanId: target.id, targetDatasetId: datasetId,
      targetName: target.name, rowIds: rows.map((r) => r.id), move, linked,
    });
  }

  function apply() {
    if (move && linkedCount > 0) setAsking(true);
    else send("refuse");
  }

  const n = rows.length;
  return (
    <ModalShell label="Copy to plan" onClose={onClose} closeOnBackdrop>
      <h3>Copy to plan</h3>
      <p>
        {move ? "Moves" : "Copies"} {n} selected row{n !== 1 ? "s" : ""} into another plan. Each keeps its
        folder and the values that plan also has per row, and starts as pending.
      </p>
      {targets.length === 0 ? (
        <p style={{ fontSize: 12.5, color: "var(--fg-mute)" }}>This dataset has no other plan. Make one in the rail first.</p>
      ) : (
        <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", flexDirection: "column", gap: 4 }}>
          Plan
          <select className="input" aria-label="Target plan" value={targetId} disabled={asking}
            onChange={(e) => setTargetId(e.target.value)}>
            {targets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      )}
      <div role="radiogroup" aria-label="Copy or move" style={{ display: "flex", gap: 14, fontSize: 12.5, marginTop: 10 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <input type="radio" name="copy-or-move" checked={!move} disabled={asking} onChange={() => setMove(false)} />
          Copy
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <input type="radio" name="copy-or-move" checked={move} disabled={asking} onChange={() => setMove(true)} />
          Move (removes them from this plan)
        </label>
      </div>
      {asking ? (
        <div role="alertdialog" aria-label="Move blueprint rows" data-testid="comfy-linked-confirm" style={{
          display: "flex", flexDirection: "column", gap: 8, padding: "10px 12px", marginTop: 12,
          border: "1px solid var(--warn)", background: "var(--warn-bg)", borderRadius: "var(--r)", fontSize: 12.5,
        }}>
          <span style={{ lineHeight: 1.45 }}>
            {linkedCount} of these {n} row{n !== 1 ? "s" : ""} came from the blueprint. Moving them takes them
            out of this plan, and the blueprint then counts their folder as short, so the next Top up refills it.
          </span>
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "flex-end", gap: 8 }}>
            <button className="btn ghost" onClick={() => setAsking(false)}>Cancel</button>
            <button className="btn" disabled={mutation.isPending} onClick={() => send("skip")}>
              Move hand-made rows only
            </button>
            <button className="btn primary" disabled={mutation.isPending} onClick={() => send("unlink")}>
              Move and unlink
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!target || mutation.isPending} onClick={apply}>
            {mutation.isPending ? (move ? "Moving…" : "Copying…") : (move ? "Move" : "Copy")}
          </button>
        </div>
      )}
    </ModalShell>
  );
}
