interface Props {
  count: number;
  hasPromptPin: boolean;
  /** A run is live on this plan: folders are locked and nothing else may start. */
  isRunning: boolean;
  onSetFolder: () => void;
  onCopyToPlan: () => void;
  onSaveToLibrary: () => void;
  onRunSelected: () => void;
  onDelete: () => void;
  onClear: () => void;
}

/**
 * The actions on the ticked rows (M6 §8d), between the Rows toolbar and the table.
 *
 * The slot is always there, a hint when nothing is ticked: the user's ruling on
 * 2026-11-05, so the table never moves under the pointer on the first tick (the
 * mockup showed the bar only with a selection, pushing the table down). Each action
 * opens the dialog the toolbar button did, so the blueprint ask, Copy's Move and a
 * new library category all survive.
 */
export default function ComfyRowsSelectionBar({
  count, hasPromptPin, isRunning,
  onSetFolder, onCopyToPlan, onSaveToLibrary, onRunSelected, onDelete, onClear,
}: Props) {
  const promptReason = "Mark a pinned parameter as the prompt first";
  // aria-disabled, not `disabled`: `.btn:disabled` sets pointer-events: none, which
  // would hide the title that says why. The click is swallowed instead.
  const act = (label: string, onClick: () => void, off: string | null, title: string, style?: React.CSSProperties) => (
    <button
      className="btn ghost sm"
      style={style}
      aria-disabled={off ? true : undefined}
      title={off ?? title}
      onClick={() => { if (!off) onClick(); }}
    >
      {label}
    </button>
  );
  return (
    <div
      className={`comfy-selbar${count > 0 ? " on" : ""}`}
      data-testid="comfy-selection-bar"
      role="toolbar"
      aria-label="Selected rows"
    >
      {count === 0 ? (
        <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>Tick rows to move, copy, run or delete them.</span>
      ) : (
        <>
          <span style={{ fontSize: 12, fontWeight: 600 }}>{count} selected</span>
          {act("Set folder…", onSetFolder, isRunning ? "Folders are locked while this plan runs" : null,
            "Set the destination folder on the selected rows")}
          {act("Copy to plan…", onCopyToPlan, null,
            "Copy or move the selected rows into another plan, keeping their folders")}
          {act("Save to library…", onSaveToLibrary, hasPromptPin ? null : promptReason,
            "Save the selected rows' prompts to the prompt library")}
          {act("Run selected", onRunSelected, isRunning ? "This plan is already running" : null,
            "Run only the selected rows, whatever their status")}
          {act("Delete", onDelete, null, "Delete the selected rows (asks first)", { color: "var(--bad)" })}
          <div style={{ flex: 1 }} />
          {act("Clear", onClear, null, "Untick every row")}
        </>
      )}
    </div>
  );
}
