import { useState } from "react";
import ModalShell from "../common/ModalShell";
import { LabelCheckList } from "../common/LabelPicker";
import { useAssignImageLabels, useLabels } from "../../hooks/useLabels";
import type { CompareImage } from "../../types";

interface Props {
  item: CompareImage;
  onClose: () => void;
}

/**
 * Labels for one image of a comparison. A dialog rather than `LabelPicker`'s
 * popover: a pane clips its overflow, which is exactly the case where an absolute
 * panel gets cut off (docs/dev/menus-popovers.md).
 *
 * Writes through `useAssignImageLabels`, the detail page's own queue, so two
 * quick toggles reach the server in the order made. Its settle invalidates the
 * label scope, which the Compare page hears (any `["images"]`/`["image"]`
 * invalidation refetches it), so the ticks follow the server.
 */
export default function CompareLabelsDialog({ item, onClose }: Props) {
  const { labels } = useLabels();
  const assign = useAssignImageLabels(item.id);
  // Ticks flip at once, as on the detail page. A toggle stays in `pending` until
  // the server's list agrees with it, so a refetch landing between two quick
  // toggles (the first one's) cannot undo the second on screen; a failed write
  // drops its entry, and the server's list shows again.
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const settled = Object.entries(pending).filter(([id, on]) => item.label_ids.includes(id) === on);
  if (settled.length > 0) {
    const rest = { ...pending };
    for (const [id] of settled) delete rest[id];
    setPending(rest);
  }
  const isOn = (id: string) => (id in pending ? pending[id] : item.label_ids.includes(id));
  const ticked = labels.map((l) => l.id).filter(isOn);
  const toggle = (id: string) => {
    const on = isOn(id);
    setPending((p) => ({ ...p, [id]: !on }));
    assign.mutate(on ? { remove: [id] } : { add: [id] }, {
      onError: () => setPending((p) => { const rest = { ...p }; delete rest[id]; return rest; }),
    });
  };

  return (
    <ModalShell label={`Labels for ${item.filename}`} onClose={onClose} closeOnBackdrop>
      <h3>Labels</h3>
      <p className="cmp-dialog-lede mono">{item.filename}</p>
      {labels.length === 0 ? (
        <p className="cmp-dialog-lede">No labels yet. Create them in Settings → Labels.</p>
      ) : (
        <LabelCheckList
          labels={labels}
          selected={ticked}
          onToggle={toggle}
          ariaLabel={`Labels for ${item.filename}`}
          autoFocus
        />
      )}
      <div className="cmp-dialog-foot">
        <button type="button" className="btn" onClick={onClose}>Done</button>
      </div>
    </ModalShell>
  );
}
