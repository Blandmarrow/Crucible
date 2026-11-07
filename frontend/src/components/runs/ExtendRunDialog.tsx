import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { runsApi, type Run, type RunLoopSettings } from "../../api/runs";
import ModalShell from "../common/ModalShell";
import NumberField from "../common/NumberField";
import { apiErrorDetail } from "../../utils/apiError";

const clampInt = (lo: number, hi: number) => (n: number) => Math.min(hi, Math.max(lo, Math.round(n)));

/** The loop's caps, by the key the run records for the one that stopped it. */
const CAPS: { cap: string; field: keyof RunLoopSettings; label: string; hi: number }[] = [
  { cap: "passes", field: "max_passes", label: "Most passes", hi: 50 },
  { cap: "images", field: "max_images", label: "Most images", hi: 100000 },
  { cap: "minutes", field: "max_minutes", label: "Most minutes", hi: 10080 },
  { cap: "paid_calls", field: "max_paid_calls", label: "Most paid LLM calls", hi: 1000000 },
];

/**
 * Carry on a run that stopped on a cap (M6 §6). The caps start at the run's own; the
 * one that stopped it must go up and none may go down, which the server checks too.
 * The run adds the next pass and a fresh tail, and the tail captions only the new
 * images. What the run has already used still counts against the raised caps.
 */
export default function ExtendRunDialog({ run, onClose, onDone }: { run: Run; onClose: () => void; onDone: () => void }) {
  const start = run.recipe.loop!;
  const [caps, setCaps] = useState<RunLoopSettings>(start);
  const stopped = run.outcome?.cap ?? null;
  const shown = CAPS.filter((c) => c.field !== "max_paid_calls" || start.max_paid_calls != null);
  const stopping = CAPS.find((c) => c.cap === stopped);
  const raised = !stopping || (caps[stopping.field] ?? 0) > (start[stopping.field] ?? 0);

  const extend = useMutation({
    mutationFn: () => runsApi.extend(run.id, caps),
    onSuccess: () => { toast.success("The run carries on"); onDone(); },
    onError: (err) => toast.error(apiErrorDetail(err, "The run could not carry on")),
  });

  return (
    <ModalShell label="Raise the caps and carry on" onClose={onClose} closeOnBackdrop>
      <h3 style={{ marginTop: 0 }}>Raise the caps and carry on</h3>
      <p style={{ fontSize: 12.5, color: "var(--fg-mute)" }}>
        Stopped: {run.outcome?.reason ?? "a cap was reached"}. The run adds another pass for the folders still short,
        then captions and exports again. Captions go only to the new images. What it has used so far still counts.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "auto 120px", gap: "8px 14px", alignItems: "center", margin: "12px 0" }}>
        {shown.map((c) => (
          <label key={c.cap} style={{ display: "contents" }}>
            <span style={{ fontSize: 12.5, fontWeight: c.cap === stopped ? 600 : undefined }}>
              {c.label}{c.cap === stopped ? " (stopped the run)" : ""}
            </span>
            <NumberField className="input" aria-label={c.label} value={caps[c.field] ?? 0}
              clamp={clampInt(start[c.field] ?? 1, c.hi)}
              onCommit={(n) => setCaps((p) => ({ ...p, [c.field]: n }))} />
          </label>
        ))}
      </div>
      <div className="row-flex" style={{ gap: 10, justifyContent: "flex-end" }}>
        {!raised && <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>Raise the cap that stopped the run.</span>}
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!raised || extend.isPending} onClick={() => extend.mutate()}>
          {extend.isPending ? "Starting…" : "Carry on"}
        </button>
      </div>
    </ModalShell>
  );
}
