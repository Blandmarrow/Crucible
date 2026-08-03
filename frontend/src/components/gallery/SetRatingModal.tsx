import { Star } from "lucide-react";

import { RATING_OPTIONS } from "../../constants/rating";
import { useModalBehavior } from "../../hooks/useModalBehavior";

interface Props {
  count: number;
  isPending: boolean;
  /** `null` clears the rating on every selected image. */
  onConfirm: (rating: number | null) => void;
  onClose: () => void;
  /** Rendered under the title when the selection spans multiple datasets. */
  sourceInfo?: React.ReactNode;
}

/**
 * Set the keep/cut rating on a selection.
 *
 * One click per tier rather than a picker plus an Apply button: this is a
 * one-field decision, and the keyboard path (1–4 in the gallery) already exists
 * for the fast case — this modal is for the times the selection came from
 * somewhere the keys are awkward, like select-all-matching-filters.
 *
 * Select-all-matching needs nothing special here: that scope is resolved to
 * concrete ids before the toolbar ever sees it, so the request looks the same.
 */
export default function SetRatingModal({ count, isPending, onConfirm, onClose, sourceInfo }: Props) {
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Set rating" });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" {...overlayProps}>
      <div className="card p-5 w-full max-w-md space-y-3" {...panelProps}>
        <h4 className="font-medium flex items-center gap-2">
          <Star size={15} /> Set Rating — {count} Image{count !== 1 ? "s" : ""}
        </h4>
        {sourceInfo}
        <p className="text-[11px] text-gray-400">
          Your keep/cut decision. In the gallery you can press <strong>1</strong>–<strong>4</strong> on a
          selection instead, and <strong>0</strong> to clear.
        </p>

        <div className="space-y-1.5">
          {RATING_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              disabled={isPending}
              data-testid={`set-rating-${opt.value}`}
              onClick={() => onConfirm(opt.value)}
              className="btn-ghost w-full flex items-center gap-3 justify-start disabled:opacity-50"
            >
              <span
                className="inline-grid place-content-center rounded"
                style={{
                  width: 22, height: 22, border: `1px solid ${opt.color}`, color: opt.color,
                  font: '600 12px "Geist Mono", monospace',
                }}
              >
                {opt.value}
              </span>
              {opt.label}
            </button>
          ))}
        </div>

        <div className="flex justify-between items-center pt-1">
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={isPending}
            onClick={() => onConfirm(null)}
            title="Remove the rating from every selected image"
          >
            Clear rating
          </button>
          <button type="button" className="btn-ghost btn-sm" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
