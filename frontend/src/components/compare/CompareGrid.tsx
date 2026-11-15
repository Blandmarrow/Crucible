import { Check } from "lucide-react";
import type { CompareImage } from "../../types";
import { imagesApi } from "../../api/images";
import { COMPARE_SIDE_MAX, autoGridColumns } from "../../utils/compare";

interface Props {
  items: CompareImage[];
  /** A column count, or "auto" to fit the number of images. */
  columns: number | "auto";
  ticked: string[];
  onToggle: (id: string) => void;
}

/**
 * Grid: every image in an equal cell, no linked zoom. Ticking up to four and
 * pressing Compare ticked (in the toolbar) opens them side by side. The grid is
 * also what Export sheet draws, so what it shows is the sheet's layout.
 *
 * Cells show thumbnails, not the full files: a grid of a hundred large PNGs would
 * decode hundreds of megabytes to draw cells a few hundred pixels wide.
 */
export default function CompareGrid({ items, columns, ticked, onToggle }: Props) {
  const cols = columns === "auto" ? autoGridColumns(items.length) : columns;
  const full = ticked.length >= COMPARE_SIDE_MAX;
  return (
    <div
      className="cmp-grid"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
      data-testid="compare-grid"
    >
      {items.map((r) => {
        const on = ticked.includes(r.id);
        return (
          <button
            key={r.id}
            type="button"
            className="cmp-cell"
            aria-pressed={on}
            disabled={!on && full}
            title={!on && full ? `Side by side takes up to ${COMPARE_SIDE_MAX}. Untick one first.` : r.filename}
            onClick={() => onToggle(r.id)}
          >
            <span className="cmp-cell-img">
              <img src={imagesApi.thumbnailUrlVersioned(r.id, r.updated_at)} alt="" loading="lazy" draggable={false} />
              <span className="cmp-tick on-media" aria-hidden>{on && <Check size={12} strokeWidth={3} />}</span>
            </span>
            <span className="cmp-cell-meta">
              <span className="cmp-cell-name mono">{r.filename}</span>
              {r.aesthetic_score != null && <span className="cmp-cell-score">{r.aesthetic_score.toFixed(2)}</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}
