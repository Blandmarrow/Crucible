import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, ChevronLeft, X } from "lucide-react";
import type { CompareImage } from "../../types";
import { imagesApi } from "../../api/images";
import { useViewportPane } from "../../hooks/useLinkedViewport";
import type { Geometry, LinkedViewport, Size } from "../../hooks/useLinkedViewport";
import MetaStrip from "./MetaStrip";
import type { BestMarks } from "./MetaStrip";

interface Props {
  item: CompareImage;
  index: number;
  vp: LinkedViewport;
  /** Match pixels: the first image's natural size, which every pane scales by. */
  refSize: Size | null;
  pixelated: boolean;
  best: BestMarks;
  showDataset: boolean;
  onSize: (id: string, size: Size) => void;
  /** Hands the page a way to measure this pane now (the toolbar's 100% asks the
   *  first pane); called with null on unmount. */
  registerMeasure: (id: string, measure: (() => Geometry | null) | null) => void;
  onOpen: () => void;
  onMoveLeft?: () => void;
  onRemove: () => void;
}

/**
 * One pane of Side by side. The thumbnail shows at once and the full file swaps
 * in when it has decoded, so four large PNGs never leave four blank panes. The
 * image is placed from its *natural* size, as the browser draws it (EXIF
 * orientation applied), not from the stored width and height.
 */
export default function ComparePane({
  item, index, vp, refSize, pixelated, best, showDataset, onSize, registerMeasure, onOpen, onMoveLeft, onRemove,
}: Props) {
  const paneRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const zoomRef = useRef<HTMLSpanElement>(null);
  const [size, setSize] = useState<Size | null>(null);
  // Set by the first layout, not by the load: between the two the full image has
  // no size or position yet, and showing it would flash it unscaled for a frame.
  const [placed, setPlaced] = useState(false);
  const [failed, setFailed] = useState(false);

  const measure = useViewportPane(paneRef, imgRef, {
    vp,
    size,
    refSize,
    onLayout: (g) => {
      if (zoomRef.current) zoomRef.current.textContent = `shown at ${Math.round(g.scale * 100)}%`;
      setPlaced(true);
    },
  });

  useEffect(() => {
    registerMeasure(item.id, measure);
    return () => registerMeasure(item.id, null);
  }, [registerMeasure, item.id, measure]);

  return (
    <div className="cmp-pane">
      <div
        ref={paneRef}
        className="cmp-vp"
        tabIndex={0}
        data-testid="compare-pane"
        aria-label={`${item.filename}. Scroll or press + and − to zoom, drag or use the arrow keys to pan, 0 to fit.`}
      >
        {!placed && !failed && (
          <img className="cmp-thumb" src={imagesApi.thumbnailUrlVersioned(item.id, item.updated_at)} alt="" draggable={false} />
        )}
        {failed ? (
          <div className="cmp-missing">This image's file could not be loaded.</div>
        ) : (
          <img
            ref={imgRef}
            className="cmp-full"
            src={imagesApi.fileUrlVersioned(item.id, item.updated_at)}
            alt={item.filename}
            decoding="async"
            draggable={false}
            // Only `visibility` and `imageRendering` are React's: the size and
            // transform are written by the layout, and React never touches them.
            style={{ visibility: placed ? "visible" : "hidden", imageRendering: pixelated ? "pixelated" : undefined }}
            onLoad={(e) => {
              const s = { w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight };
              setSize(s);
              onSize(item.id, s);
            }}
            onError={() => setFailed(true)}
          />
        )}
        <span className="cmp-index on-media" aria-hidden>{index + 1}</span>
        <div className="cmp-corner on-media">
          <button type="button" className="cmp-corner-btn" title="Open in the detail view" aria-label={`Open ${item.filename} in the detail view`} onClick={onOpen}>
            <ArrowUpRight size={14} />
          </button>
          {onMoveLeft && (
            <button type="button" className="cmp-corner-btn" title="Move left" aria-label={`Move ${item.filename} left`} onClick={onMoveLeft}>
              <ChevronLeft size={14} />
            </button>
          )}
          <button type="button" className="cmp-corner-btn" title="Remove from the comparison" aria-label={`Remove ${item.filename} from the comparison`} onClick={onRemove}>
            <X size={14} />
          </button>
        </div>
      </div>
      <MetaStrip item={item} best={best} showDataset={showDataset} zoomRef={zoomRef} />
    </div>
  );
}
