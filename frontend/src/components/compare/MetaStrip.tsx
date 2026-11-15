import { useState } from "react";
import type { CompareImage } from "../../types";
import { formatSize } from "../datasets/format";
import { aestheticModelLabel } from "../../constants/aestheticModels";

export interface BestMarks {
  /** This image has the most pixels of those shown, and they are not all equal. */
  pixels: boolean;
  /** This image has the highest aesthetic score, among scores on one scale. */
  score: boolean;
}

interface Props {
  item: CompareImage;
  best: BestMarks;
  /** Name the dataset: the comparison spans more than one. */
  showDataset: boolean;
  /** Receives the "shown at N%" span, which the pane rewrites on every layout
   *  without a React render (see `useViewportPane`). */
  zoomRef: React.Ref<HTMLSpanElement>;
}

/** The facts under one pane. Best values are marked in words, never by colour
 *  alone, so they read in both themes and to colour-blind users. */
export default function MetaStrip({ item, best, showDataset, zoomRef }: Props) {
  const [captionOpen, setCaptionOpen] = useState(false);
  const caption = item.caption_text.trim();

  return (
    <div className="cmp-strip">
      <div className="cmp-name mono" title={item.filename}>{item.filename}</div>
      <div className="cmp-facts">
        {item.width != null && item.height != null && (
          <span>
            {item.width}×{item.height}
            {best.pixels && <span className="cmp-best">most px</span>}
          </span>
        )}
        {item.file_size_bytes != null && <span>{formatSize(item.file_size_bytes)}</span>}
        {item.format && <span>{item.format.toUpperCase()}</span>}
        {item.aesthetic_score != null && (
          <span title={item.scores_stale ? "Measured before the pixels were last rewritten" : undefined}>
            aesthetic {item.aesthetic_score.toFixed(2)}
            {item.aesthetic_model && ` (${aestheticModelLabel(item.aesthetic_model)})`}
            {item.scores_stale && " · stale"}
            {best.score && <span className="cmp-best">best</span>}
          </span>
        )}
        {showDataset && <span>{item.dataset_name}</span>}
        <span ref={zoomRef} />
      </div>
      {caption && (
        <button
          type="button"
          className={`cmp-caption${captionOpen ? " open" : ""}`}
          aria-expanded={captionOpen}
          title={captionOpen ? "Fold the caption" : "Show the whole caption"}
          onClick={() => setCaptionOpen((o) => !o)}
        >
          {caption}
        </button>
      )}
    </div>
  );
}
