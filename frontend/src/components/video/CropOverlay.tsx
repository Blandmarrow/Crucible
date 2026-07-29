import { useCallback, useEffect, useRef, useState } from "react";
import NumberField from "../common/NumberField";
import type { CropRect } from "../../types";

interface Props {
  /** Sample frame to draw under the overlay — a `data:` URL from the probe. */
  src: string;
  /** The *video's* dimensions, not the sample's. The sample is downscaled to
   *  `max_edge`, but a crop rect is stored in frame coordinates. */
  frameW: number;
  frameH: number;
  rect: CropRect | null;
  onChange: (rect: CropRect | null) => void;
}

type Edge = "l" | "r" | "t" | "b";

/** Even numbers only, mirroring `video_frames.clamp_crop`, so the rect shown here
 *  is the rect the backend stores. Odd coordinates break chroma subsampling on
 *  half the codecs and the server would snap them anyway — silently, which is
 *  worse than snapping under the user's finger. */
function snapEven(n: number): number {
  return Math.round(n / 2) * 2;
}

const MIN_SIDE = 16;

/**
 * Draggable-edge crop overlay for the extraction modal.
 *
 * Trimming a letterbox matte is a see-it-to-set-it task, so the primary control
 * is the picture itself: four shaded mattes outside the rect and four edge
 * handles. The numeric x/y/w/h inputs beneath are not a fallback — they are the
 * keyboard path, and the handles are `aria-hidden` because there is no honest
 * ARIA pattern for a 2-D rect (slider semantics describe one axis and would
 * announce nonsense on the other).
 *
 * Pointer events with `setPointerCapture`, never mouse events: a drag that leaves
 * the element must keep tracking and must still end when the button comes up
 * outside it.
 *
 * The rect here is a *proposal*. `POST /videos/extract` re-normalizes (even-snap,
 * and a full-frame rect is stored as no crop at all), and the stored value is the
 * one a later re-extraction replays — so the modal re-reads `Video.crop_*` after
 * a successful extract rather than trusting what it sent.
 */
export default function CropOverlay({ src, frameW, frameH, rect, onChange }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [displayW, setDisplayW] = useState(0);
  const dragRef = useRef<{ edge: Edge; rect: CropRect } | null>(null);

  // The displayed width drives frame↔screen conversion, and it changes with the
  // panel (a modal at 94vw on a narrow window). ResizeObserver rather than a
  // one-shot measure, or every handle lands in the wrong place after a resize.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setDisplayW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const scale = displayW && frameW ? displayW / frameW : 0;
  const full: CropRect = { x: 0, y: 0, w: frameW, h: frameH };
  const active = rect ?? full;

  const clampRect = useCallback(
    (r: CropRect): CropRect => {
      const x = Math.max(0, Math.min(snapEven(r.x), frameW - MIN_SIDE));
      const y = Math.max(0, Math.min(snapEven(r.y), frameH - MIN_SIDE));
      return {
        x,
        y,
        w: Math.max(MIN_SIDE, Math.min(snapEven(r.w), frameW - x)),
        h: Math.max(MIN_SIDE, Math.min(snapEven(r.h), frameH - y)),
      };
    },
    [frameW, frameH],
  );

  function handlePointerDown(edge: Edge, e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = { edge, rect: active };
  }

  function handlePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || !scale || !boxRef.current) return;
    const bounds = boxRef.current.getBoundingClientRect();
    // Frame coordinates throughout — the display scale is an implementation
    // detail of the picture, not of the value being edited.
    const fx = (e.clientX - bounds.left) / scale;
    const fy = (e.clientY - bounds.top) / scale;
    const r = { ...drag.rect };
    if (drag.edge === "l") {
      const right = r.x + r.w;
      r.x = Math.max(0, Math.min(fx, right - MIN_SIDE));
      r.w = right - r.x;
    } else if (drag.edge === "r") {
      r.w = Math.max(MIN_SIDE, Math.min(fx, frameW) - r.x);
    } else if (drag.edge === "t") {
      const bottom = r.y + r.h;
      r.y = Math.max(0, Math.min(fy, bottom - MIN_SIDE));
      r.h = bottom - r.y;
    } else {
      r.h = Math.max(MIN_SIDE, Math.min(fy, frameH) - r.y);
    }
    onChange(clampRect(r));
  }

  function endDrag(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return;
    dragRef.current = null;
    const el = e.currentTarget as HTMLElement;
    if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
  }

  const px = (n: number) => `${n * scale}px`;
  const matte = "rgba(7,9,11,.62)";
  const handleStyle: React.CSSProperties = {
    position: "absolute",
    background: "var(--accent)",
    opacity: 0.85,
    touchAction: "none",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        ref={boxRef}
        style={{ position: "relative", width: "100%", lineHeight: 0, userSelect: "none" }}
      >
        <img
          src={src}
          alt="Sample frame"
          draggable={false}
          style={{ width: "100%", display: "block", borderRadius: "var(--r-md)" }}
        />
        {scale > 0 && (
          <>
            {/* Four mattes rather than one box with a hole: a single outlined
                rect gives no sense of how much is being thrown away, which is
                the whole reason to look at the picture. */}
            <div style={{ position: "absolute", left: 0, top: 0, width: "100%", height: px(active.y), background: matte, pointerEvents: "none" }} />
            <div style={{ position: "absolute", left: 0, top: px(active.y + active.h), width: "100%", bottom: 0, background: matte, pointerEvents: "none" }} />
            <div style={{ position: "absolute", left: 0, top: px(active.y), width: px(active.x), height: px(active.h), background: matte, pointerEvents: "none" }} />
            <div style={{ position: "absolute", left: px(active.x + active.w), top: px(active.y), right: 0, height: px(active.h), background: matte, pointerEvents: "none" }} />

            {/* Handles. aria-hidden on purpose — the numeric inputs below are the
                keyboard and screen-reader path for this control. */}
            <div
              aria-hidden
              data-testid="crop-handle-l"
              onPointerDown={(e) => handlePointerDown("l", e)}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              style={{ ...handleStyle, left: px(active.x) , top: px(active.y), width: 6, height: px(active.h), marginLeft: -3, cursor: "ew-resize" }}
            />
            <div
              aria-hidden
              data-testid="crop-handle-r"
              onPointerDown={(e) => handlePointerDown("r", e)}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              style={{ ...handleStyle, left: px(active.x + active.w), top: px(active.y), width: 6, height: px(active.h), marginLeft: -3, cursor: "ew-resize" }}
            />
            <div
              aria-hidden
              data-testid="crop-handle-t"
              onPointerDown={(e) => handlePointerDown("t", e)}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              style={{ ...handleStyle, left: px(active.x), top: px(active.y), width: px(active.w), height: 6, marginTop: -3, cursor: "ns-resize" }}
            />
            <div
              aria-hidden
              data-testid="crop-handle-b"
              onPointerDown={(e) => handlePointerDown("b", e)}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              style={{ ...handleStyle, left: px(active.x), top: px(active.y + active.h), width: px(active.w), height: 6, marginTop: -3, cursor: "ns-resize" }}
            />
          </>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 11.5, color: "var(--fg-mute)" }}>
        {(["x", "y", "w", "h"] as const).map((field) => (
          <label key={field} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ textTransform: "uppercase" }}>{field}</span>
            {/* `NumberField`, not a bare input: re-clamping each keystroke made
                the documented keyboard path lie about what was typed — `800`
                into W became `1600`, and the even-snap turned `150` into `250`.
                `clampRect` is projected onto the one field so both props stay
                honest about the cross-field bounds. Editing while `rect` is null
                still creates a crop from the full frame — that is how this path
                creates a rect; only the *no-typing* case changes. */}
            <NumberField
              className="input"
              step={2}
              min={0}
              aria-label={`Crop ${field}`}
              style={{ width: 66, fontSize: 11.5 }}
              value={active[field]}
              clamp={(n) => clampRect({ ...active, [field]: n })[field]}
              onCommit={(n) => onChange(clampRect({ ...active, [field]: n }))}
            />
          </label>
        ))}
        <span style={{ color: "var(--fg-dim)" }}>of {frameW}×{frameH}</span>
      </div>
    </div>
  );
}
