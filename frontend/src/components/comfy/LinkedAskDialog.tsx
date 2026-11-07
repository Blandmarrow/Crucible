import ModalShell from "../common/ModalShell";
import type { LinkedMode } from "../../api/comfy";

interface Props {
  /** Rows the drop would move that the blueprint counts, of `total` moving. */
  linked: number;
  total: number;
  folder: string;
  busy: boolean;
  onChoose: (linked: Exclude<LinkedMode, "refuse">) => void;
  onCancel: () => void;
}

/** A drop that would move rows the blueprint counts asks first, with Set folder's three
 *  choices (the plan's ruling 2): the drop has no form of its own to ask in. */
export default function LinkedAskDialog({ linked, total, folder, busy, onChoose, onCancel }: Props) {
  return (
    <ModalShell label="Move blueprint rows" onClose={onCancel} closeOnBackdrop>
      <h3>Move blueprint rows</h3>
      <div data-testid="comfy-linked-confirm">
        <p style={{ lineHeight: 1.45 }}>
          {linked} of these {total} row{total !== 1 ? "s" : ""} came from the blueprint. Moving them to{" "}
          <span className="mono">{folder || "(root)"}</span> unlinks them, and the blueprint then counts their
          old folder as short, so the next Top up refills it.
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button className="btn ghost" onClick={onCancel}>Cancel</button>
          <button className="btn" disabled={busy} onClick={() => onChoose("skip")}>Move hand-made rows only</button>
          <button className="btn primary" disabled={busy} onClick={() => onChoose("unlink")}>Move and unlink</button>
        </div>
      </div>
    </ModalShell>
  );
}
