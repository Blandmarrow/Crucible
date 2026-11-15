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
  // Ticks flip at once, as on the detail page; the server's list replaces them
  // whenever it arrives (the assign queue refetches once its last write settles,
  // and a failed write's refetch is its rollback).
  const serverKey = item.label_ids.join(",");
  const [seen, setSeen] = useState(serverKey);
  const [ticked, setTicked] = useState<string[]>(item.label_ids);
  if (seen !== serverKey) {
    setSeen(serverKey);
    setTicked(item.label_ids);
  }
  const toggle = (id: string) => {
    const on = ticked.includes(id);
    setTicked(on ? ticked.filter((x) => x !== id) : [...ticked, id]);
    assign.mutate(on ? { remove: [id] } : { add: [id] });
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
