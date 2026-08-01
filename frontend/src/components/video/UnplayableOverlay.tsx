import { AlertTriangle } from "lucide-react";

/**
 * The "your browser can't play this" panel, drawn over the poster frame.
 *
 * Over rather than instead of: the poster is proof the file decodes, so keeping
 * it visible behind the message is half the reassurance. The message itself
 * comes from `utils/videoPlayback.ts::playbackErrorMessage` — this component
 * never decides *whether* to appear, only how.
 */
export default function UnplayableOverlay({ message }: { message: string }) {
  return (
    <div
      role="status"
      style={{
        position: "absolute", inset: 0,
        display: "grid", placeContent: "center", padding: 16,
        background: "rgba(7,9,11,.72)",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          display: "flex", gap: 10, maxWidth: 420,
          background: "var(--surface-1)", border: "1px solid var(--line)",
          borderRadius: "var(--r-lg)", padding: "12px 14px",
          pointerEvents: "auto",
        }}
      >
        <AlertTriangle size={16} style={{ color: "var(--warn)", flexShrink: 0, marginTop: 1 }} />
        <p style={{ fontSize: 12.5, lineHeight: 1.5, color: "var(--fg)" }}>{message}</p>
      </div>
    </div>
  );
}
