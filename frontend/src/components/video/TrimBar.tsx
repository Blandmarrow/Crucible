import { useRef, useState } from "react";
import { formatDuration } from "../../utils/duration";

interface Props {
  durationMs: number;
  startMs: number;
  /** Milliseconds cut off the **tail**, not an end position — the backend
   *  computes `end = duration_ms - trim_end_ms`. The right handle therefore sits
   *  at `duration - endMs` and reports back the difference. */
  endMs: number;
  onChange: (startMs: number, endMs: number) => void;
  disabled?: boolean;
  /** Shown instead of the handles when the control is disabled. */
  disabledNote?: string;
}

type Handle = "start" | "end";

const MIN_SPAN_MS = 500;

/**
 * Two-handle trim track over a video's duration.
 *
 * The asymmetry in the props is the backend's, not this component's: a tail trim
 * is expressed as a *length cut*, so a clip whose duration is corrected later
 * keeps trimming the same amount of tail rather than jumping to a stale absolute
 * position. This renders it as a position and converts on the way out.
 *
 * Disabled with an explanation when the container will not seek
 * (`duration_source === "unknown"`) — the backend already warns that the tail
 * trim is ignored there, and offering a control that does nothing is worse than
 * offering none.
 */
export default function TrimBar({ durationMs, startMs, endMs, onChange, disabled, disabledNote }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState<Handle | null>(null);

  const duration = Math.max(durationMs, 1);
  const endPos = Math.max(0, duration - endMs);
  const pct = (ms: number) => `${Math.max(0, Math.min(100, (ms / duration) * 100))}%`;

  function positionFromEvent(clientX: number): number {
    const el = trackRef.current;
    if (!el) return 0;
    const bounds = el.getBoundingClientRect();
    const fraction = (clientX - bounds.left) / Math.max(bounds.width, 1);
    return Math.round(Math.max(0, Math.min(1, fraction)) * duration);
  }

  function move(handle: Handle, clientX: number) {
    const ms = positionFromEvent(clientX);
    if (handle === "start") {
      onChange(Math.max(0, Math.min(ms, endPos - MIN_SPAN_MS)), endMs);
    } else {
      // Back to a tail length on the way out.
      onChange(startMs, Math.max(0, duration - Math.max(ms, startMs + MIN_SPAN_MS)));
    }
  }

  function handlePointerDown(handle: Handle, e: React.PointerEvent<HTMLDivElement>) {
    if (disabled) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDragging(handle);
    move(handle, e.clientX);
  }

  function handlePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragging) return;
    move(dragging, e.clientX);
  }

  function endDrag(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragging) return;
    setDragging(null);
    const el = e.currentTarget as HTMLElement;
    if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
  }

  const handleStyle: React.CSSProperties = {
    position: "absolute",
    top: -4,
    width: 10,
    height: 22,
    marginLeft: -5,
    borderRadius: 3,
    background: "var(--accent)",
    cursor: "ew-resize",
    touchAction: "none",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <div
        ref={trackRef}
        data-testid="trim-bar"
        style={{
          position: "relative",
          height: 14,
          borderRadius: 3,
          background: "var(--surface-3)",
          opacity: disabled ? 0.4 : 1,
        }}
      >
        <div
          style={{
            position: "absolute", top: 0, bottom: 0,
            left: pct(startMs), width: `calc(${pct(endPos)} - ${pct(startMs)})`,
            background: "var(--accent)", opacity: 0.28,
          }}
        />
        {!disabled && (
          <>
            <div
              data-testid="trim-handle-start"
              role="slider"
              aria-label="Trim start"
              aria-valuemin={0}
              aria-valuemax={duration}
              aria-valuenow={startMs}
              aria-valuetext={formatDuration(startMs)}
              tabIndex={0}
              onPointerDown={(e) => handlePointerDown("start", e)}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onKeyDown={(e) => {
                const step = e.shiftKey ? 5000 : 500;
                if (e.key === "ArrowLeft") onChange(Math.max(0, startMs - step), endMs);
                if (e.key === "ArrowRight") onChange(Math.min(endPos - MIN_SPAN_MS, startMs + step), endMs);
              }}
              style={{ ...handleStyle, left: pct(startMs) }}
            />
            <div
              data-testid="trim-handle-end"
              role="slider"
              aria-label="Trim end"
              aria-valuemin={0}
              aria-valuemax={duration}
              aria-valuenow={endPos}
              aria-valuetext={formatDuration(endPos)}
              tabIndex={0}
              onPointerDown={(e) => handlePointerDown("end", e)}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onKeyDown={(e) => {
                const step = e.shiftKey ? 5000 : 500;
                if (e.key === "ArrowLeft") onChange(startMs, Math.min(duration - startMs - MIN_SPAN_MS, endMs + step));
                if (e.key === "ArrowRight") onChange(startMs, Math.max(0, endMs - step));
              }}
              style={{ ...handleStyle, left: pct(endPos) }}
            />
          </>
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--fg-dim)" }}>
        <span>{formatDuration(startMs)}</span>
        <span>
          {disabled
            ? disabledNote ?? "Trimming is unavailable for this container"
            : `${formatDuration(Math.max(0, endPos - startMs))} of ${formatDuration(duration)}`}
        </span>
        <span>{formatDuration(endPos)}</span>
      </div>
    </div>
  );
}
