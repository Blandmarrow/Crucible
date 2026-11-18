import { useId } from "react";
import { GOAL_MAX_LEN, TARGET_RESOLUTION_MAX, TARGET_RESOLUTION_MIN, parseTarget, type GoalDraft } from "./goalDraft";

/**
 * The analyst's goal fields (M12) for the New and Edit dataset dialogs. Both are
 * optional: the goal says what the dataset is for (separate from the description,
 * which usually says where the images came from), and the target resolution is the
 * short side the Health page checks images against.
 */
export default function GoalFields({ value, onChange }: { value: GoalDraft; onChange: (v: GoalDraft) => void }) {
  const uid = useId();
  const targetInvalid = parseTarget(value.target) === undefined;
  const note = { fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 } as const;
  return (
    <>
      <div>
        <label className="label" htmlFor={`${uid}-goal`}>
          Goal <span style={note}>(optional — what the dataset is for)</span>
        </label>
        <textarea
          id={`${uid}-goal`}
          className="input"
          rows={2}
          maxLength={GOAL_MAX_LEN}
          placeholder="e.g. SDXL style LoRA of watercolour landscapes"
          value={value.goal}
          onChange={(e) => onChange({ ...value, goal: e.target.value })}
          style={{ resize: "vertical" }}
        />
      </div>
      <div>
        <label className="label" htmlFor={`${uid}-target`}>
          Target resolution <span style={note}>(optional — short side in px; blank uses 1024)</span>
        </label>
        <input
          id={`${uid}-target`}
          className="input"
          inputMode="numeric"
          placeholder="1024"
          value={value.target}
          aria-invalid={targetInvalid || undefined}
          onChange={(e) => onChange({ ...value, target: e.target.value })}
          style={{ maxWidth: 140 }}
        />
        {targetInvalid && (
          <div role="status" aria-live="polite" data-testid="target-invalid" style={{ fontSize: 11.5, color: "var(--bad)", marginTop: 4 }}>
            A whole number from {TARGET_RESOLUTION_MIN} to {TARGET_RESOLUTION_MAX}, or blank.
          </div>
        )}
      </div>
    </>
  );
}
