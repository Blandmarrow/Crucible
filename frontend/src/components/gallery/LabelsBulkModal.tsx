import { useState } from "react";
import { Tags } from "lucide-react";

import type { Label } from "../../api/labels";
import { LabelCheckList } from "../common/LabelPicker";
import { useModalBehavior } from "../../hooks/useModalBehavior";

interface Props {
  count: number;
  labels: Label[];
  isPending: boolean;
  onConfirm: (edit: { add: string[]; remove: string[] }) => void;
  onClose: () => void;
  /** Rendered under the title when the selection spans multiple datasets. */
  sourceInfo?: React.ReactNode;
}

/**
 * Bulk label editor for the gallery's `SelectionToolbar`.
 *
 * A dumb modal — form state only, no queries and no mutation. The toolbar owns
 * the request, exactly as it does for `SetProvenanceModal`, so the invalidate →
 * close → clear-selection → toast sequence lives in one place.
 *
 * Add and Remove are separate searchable checkbox lists rather than a tri-state
 * per label: "add fx to all of these" and "take reject off all of these" are the
 * two things anyone does in bulk, and one endpoint call carries both. A label
 * cannot be in both groups — the server rejects an overlapping body with a 400,
 * and ticking it in one group clears it from the other so that never happens.
 */
export default function LabelsBulkModal({
  count, labels, isPending, onConfirm, onClose, sourceInfo,
}: Props) {
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Edit labels" });
  const [add, setAdd] = useState<Set<string>>(new Set());
  const [remove, setRemove] = useState<Set<string>>(new Set());

  function toggle(which: "add" | "remove", id: string) {
    const [set, setSet, other, setOther] =
      which === "add"
        ? ([add, setAdd, remove, setRemove] as const)
        : ([remove, setRemove, add, setAdd] as const);
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSet(next);
    if (other.has(id)) {
      const cleared = new Set(other);
      cleared.delete(id);
      setOther(cleared);
    }
  }

  const nothingToDo = add.size === 0 && remove.size === 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" {...overlayProps}>
      <div className="card p-5 w-full max-w-md space-y-3 max-h-[90vh] overflow-auto" {...panelProps}>
        <h4 className="font-medium flex items-center gap-2">
          <Tags size={15} /> Labels — {count} Image{count !== 1 ? "s" : ""}
        </h4>
        {sourceInfo}

        {labels.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--fg-mute)" }}>
            No labels defined yet — create them in Settings → Labels.
          </p>
        ) : (
          <>
            <div className="space-y-1">
              <div className="label !mb-0">Add to all selected</div>
              <LabelCheckList
                labels={labels}
                selected={add}
                onToggle={(id) => toggle("add", id)}
                ariaLabel="Labels to add"
              />
            </div>

            <div className="space-y-1">
              <div className="label !mb-0">Remove from all selected</div>
              <LabelCheckList
                labels={labels}
                selected={remove}
                onToggle={(id) => toggle("remove", id)}
                ariaLabel="Labels to remove"
              />
            </div>

            <p className="text-xs" style={{ color: "var(--fg-mute)" }}>
              Adding a label an image already has changes nothing, and removing one it does not
              have is equally harmless — this is safe to re-run.
            </p>
          </>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn btn-sm" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary btn-sm"
            disabled={isPending || nothingToDo}
            onClick={() => onConfirm({ add: [...add], remove: [...remove] })}
          >
            {isPending ? "Applying…" : "Apply"}
          </button>
        </div>
      </div>
    </div>
  );
}
