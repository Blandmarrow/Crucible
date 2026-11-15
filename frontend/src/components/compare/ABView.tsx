import { useEffect, useRef, useState } from "react";
import type { CompareImage } from "../../types";
import { imagesApi } from "../../api/images";
import { usePaneContext } from "../../contexts/PaneContext";
import { usePaneStore } from "../../store/paneStore";
import { isTextEntryTarget } from "../../utils/keyboard";
import { useViewportPane } from "../../hooks/useLinkedViewport";
import type { Geometry, LinkedViewport, Size } from "../../hooks/useLinkedViewport";
import type { AbHow } from "../../utils/compare";

interface Props {
  a: CompareImage;
  b: CompareImage;
  how: AbHow;
  vp: LinkedViewport;
  pixelated: boolean;
  registerMeasure: (id: string, measure: (() => Geometry | null) | null) => void;
}

/**
 * A/B: two images in one frame, under the same linked zoom as Side by side.
 * B is drawn over A in A's rectangle, so images of one aspect line up exactly and
 * any other is stretched to fit (the hint says so). Swipe clips B to the right of a
 * divider, Flip shows B only while Space or the button is held, and Difference
 * blends B over A so unchanged pixels go black.
 *
 * Everything that follows the pointer (B's clip, the divider) is written to the DOM
 * from the pane's layout, like the image itself, so dragging never re-renders.
 */
export default function ABView({ a, b, how, vp, pixelated, registerMeasure }: Props) {
  const paneRef = useRef<HTMLDivElement>(null);
  const imgA = useRef<HTMLImageElement>(null);
  const imgB = useRef<HTMLImageElement>(null);
  const divider = useRef<HTMLDivElement>(null);
  const handle = useRef<HTMLButtonElement>(null);
  const split = useRef(0.5);
  const lastGeom = useRef<Geometry | null>(null);
  const [sizeA, setSizeA] = useState<Size | null>(null);
  const [sizeB, setSizeB] = useState<Size | null>(null);
  const [placed, setPlaced] = useState(false);
  const [flipped, setFlipped] = useState(false);

  // B follows A's rectangle; the divider and B's clip follow the split.
  const applyOverlay = (g: Geometry) => {
    const ib = imgB.current;
    if (ib) {
      ib.style.width = `${g.w}px`;
      ib.style.height = `${g.h}px`;
      ib.style.transform = `translate(${g.tx}px, ${g.ty}px)`;
      // `inset()` is in B's own box, before its transform, hence `- g.tx`.
      const left = Math.min(g.w, Math.max(0, g.W * split.current - g.tx));
      ib.style.clipPath = how === "swipe" ? `inset(0 0 0 ${left}px)` : "";
    }
    const x = `${g.W * split.current}px`;
    if (divider.current) divider.current.style.left = x;
    if (handle.current) handle.current.style.left = x;
  };

  const measure = useViewportPane(paneRef, imgA, {
    vp,
    size: sizeA,
    refSize: null,
    onLayout: (g) => {
      lastGeom.current = g;
      applyOverlay(g);
      setPlaced(true);
    },
  });

  useEffect(() => {
    registerMeasure(a.id, measure);
    return () => registerMeasure(a.id, null);
  }, [registerMeasure, a.id, measure]);

  // Switching how B is shown changes the clip without moving the view. (A held
  // flip needs no reset here: releasing Space or the button always clears it.)
  useEffect(() => {
    if (lastGeom.current) applyOverlay(lastGeom.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [how]);

  const moveSplit = (to: number) => {
    split.current = Math.min(1, Math.max(0, to));
    if (lastGeom.current) applyOverlay(lastGeom.current);
    handle.current?.setAttribute("aria-valuenow", String(Math.round(split.current * 100)));
  };

  // Hold Space to flip, with the guards every key handler in a pane needs: not
  // while typing, and only in the active pane, so Space in the gallery beside
  // this page never flips it.
  const paneCtx = usePaneContext();
  const activePaneId = usePaneStore((s) => s.activePaneId);
  useEffect(() => {
    if (how !== "flip") return;
    const mine = () => !paneCtx || paneCtx.paneId === activePaneId;
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || isTextEntryTarget(e) || !mine()) return;
      e.preventDefault();
      setFlipped(true);
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") setFlipped(false);
    };
    const blur = () => setFlipped(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, [how, paneCtx, activePaneId]);

  const showB = how !== "flip" || flipped;
  const aspectDiffers =
    sizeA && sizeB && Math.abs(sizeA.w / sizeA.h - sizeB.w / sizeB.h) > 0.01;
  const imgStyle = (visible: boolean): React.CSSProperties => ({
    visibility: visible ? "visible" : "hidden",
    imageRendering: pixelated ? "pixelated" : undefined,
  });

  return (
    <div className="cmp-ab">
      <div
        ref={paneRef}
        className="cmp-vp cmp-ab-frame"
        tabIndex={0}
        data-testid="compare-ab"
        aria-label={`A: ${a.filename}, B: ${b.filename}. Scroll or press + and − to zoom, drag or use the arrow keys to pan, 0 to fit.`}
      >
        {!placed && (
          <img className="cmp-thumb" src={imagesApi.thumbnailUrlVersioned(a.id, a.updated_at)} alt="" draggable={false} />
        )}
        <img
          ref={imgA}
          className="cmp-full"
          src={imagesApi.fileUrlVersioned(a.id, a.updated_at)}
          alt={`A: ${a.filename}`}
          decoding="async"
          draggable={false}
          style={imgStyle(placed)}
          onLoad={(e) => setSizeA({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
        />
        <img
          ref={imgB}
          className="cmp-full"
          src={imagesApi.fileUrlVersioned(b.id, b.updated_at)}
          alt={`B: ${b.filename}`}
          decoding="async"
          draggable={false}
          data-testid="compare-ab-b"
          style={{
            ...imgStyle(placed && !!sizeB && showB),
            mixBlendMode: how === "difference" ? "difference" : undefined,
          }}
          onLoad={(e) => setSizeB({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
        />

        {how === "swipe" && (
          <>
            <div ref={divider} className="cmp-ab-divider" aria-hidden />
            <button
              ref={handle}
              type="button"
              className="cmp-ab-handle"
              role="slider"
              aria-label="Divider between A and B"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={50}
              onPointerDown={(e) => {
                e.stopPropagation();
                e.currentTarget.setPointerCapture(e.pointerId);
              }}
              onPointerMove={(e) => {
                if (!e.currentTarget.hasPointerCapture(e.pointerId) || !paneRef.current) return;
                const r = paneRef.current.getBoundingClientRect();
                moveSplit((e.clientX - r.left) / r.width);
              }}
              onKeyDown={(e) => {
                // Kept from the frame, whose arrow keys pan.
                if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                  e.preventDefault();
                  e.stopPropagation();
                  moveSplit(split.current + (e.key === "ArrowLeft" ? -0.05 : 0.05));
                }
              }}
            >
              ⇔
            </button>
            <span className="cmp-ab-label on-media" style={{ left: 6 }}>A · {a.filename}</span>
            <span className="cmp-ab-label on-media" style={{ right: 6 }}>B · {b.filename}</span>
          </>
        )}
        {how === "flip" && (
          <span className="cmp-ab-label on-media" style={{ left: 6 }} data-testid="compare-ab-showing">
            {flipped ? `B · ${b.filename}` : `A · ${a.filename}`}
          </span>
        )}
        {how === "difference" && (
          <span className="cmp-ab-label on-media" style={{ left: 6 }}>
            B over A as a difference: black means the same
          </span>
        )}
      </div>
      <div className="cmp-ab-foot">
        {how === "flip" && (
          <button
            type="button"
            className="btn sm"
            onPointerDown={() => setFlipped(true)}
            onPointerUp={() => setFlipped(false)}
            onPointerLeave={() => setFlipped(false)}
            onPointerCancel={() => setFlipped(false)}
          >
            Hold to show B
          </button>
        )}
        {how === "flip" && <span>or hold <kbd className="kbd">Space</kbd></span>}
        {aspectDiffers && (
          <span>A and B have different shapes, so B is stretched to A's frame and the overlay is approximate.</span>
        )}
      </div>
    </div>
  );
}
