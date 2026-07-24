import type { CanvasWorkflowResponse, PinnedParam } from "../../api/comfy";
import { useModalBehavior } from "../../hooks/useModalBehavior";

interface Props {
  snapshot: CanvasWorkflowResponse;
  /** Pins that no longer resolve in the incoming workflow — removed on apply. */
  droppedPins: PinnedParam[];
  onApply: () => void;
  onClose: () => void;
}

// Bridge snapshots older than this get a "is the tab still open?" nudge.
const STALE_AGE_S = 300;

function formatAge(s: number): string {
  if (s < 60) return `${Math.max(1, Math.round(s))} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

/** Confirmation before replacing the plan's workflow with one pulled from ComfyUI. */
export default function SyncCanvasModal({ snapshot, droppedPins, onApply, onClose }: Props) {
  const isBridge = snapshot.source === "bridge";
  const { overlayProps, panelProps } = useModalBehavior({ onClose, label: "Sync from ComfyUI", closeOnBackdrop: true });
  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}
      {...overlayProps}
    >
      <div className="panel" style={{ width: 520, maxWidth: "94vw" }} {...panelProps}>
        <div className="panel-h">
          <h3>Sync from ComfyUI</h3>
          <div style={{ flex: 1 }} />
          <button className="icon-btn" title="Close" onClick={onClose}>×</button>
        </div>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, flexWrap: "wrap" }}>
            <span className={`badge dot${isBridge ? " good" : ""}`}>
              {isBridge ? "live canvas" : "last queued"}
            </span>
            <span>
              {isBridge ? (
                <>
                  Canvas: <b>{snapshot.name ?? "(unsaved workflow)"}</b> — {snapshot.node_count} nodes
                  {snapshot.age_seconds != null && <>, pushed {formatAge(snapshot.age_seconds)}</>}
                </>
              ) : (
                <>Last queued prompt — {snapshot.node_count} nodes</>
              )}
            </span>
          </div>

          {!isBridge && (
            <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
              The CrucibleBridge extension isn't installed, so this is the workflow from the last{" "}
              <b>Queue Prompt</b> — canvas edits made since then are not included. Install{" "}
              <span className="mono">extras/ComfyUI-CrucibleBridge</span> to sync the live canvas.
            </p>
          )}
          {isBridge && snapshot.age_seconds != null && snapshot.age_seconds > STALE_AGE_S && (
            <p style={{ color: "var(--warn, #d97706)", fontSize: 12, margin: 0 }}>
              ⚠ This snapshot is {formatAge(snapshot.age_seconds).replace(" ago", "")} old — is the ComfyUI browser tab still open?
            </p>
          )}

          {droppedPins.length > 0 && (
            <div style={{ border: "1px solid var(--warn, #d97706)", borderRadius: "var(--r)", padding: "8px 10px", fontSize: 12 }}>
              <p style={{ color: "var(--warn, #d97706)", margin: "0 0 4px" }}>
                ⚠ {droppedPins.length} pinned parameter{droppedPins.length > 1 ? "s" : ""} no longer
                resolve{droppedPins.length > 1 ? "" : "s"} in the new workflow (node removed or bypassed)
                and will be removed:
              </p>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {droppedPins.map((p) => (
                  <li key={`${p.node_id}::${p.input}`} className="mono">
                    {p.alias} (#{p.node_id} · {p.input}){p.is_prompt ? " ★ prompt" : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn primary sm" onClick={onApply}>Replace workflow</button>
          </div>
        </div>
      </div>
    </div>
  );
}
