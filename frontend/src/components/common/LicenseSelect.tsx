import { LICENSE_OPTIONS, OTHER_PREFIX } from "../../constants/licenses";

interface Props {
  /** Stored value: "", a known id, or `other:<free text>`. */
  value: string;
  onChange: (next: string) => void;
  /** Label for the empty option — its meaning differs per caller. */
  emptyLabel: string;
  disabled?: boolean;
  /** Classes for the `<select>`; also for the free-text input unless overridden. */
  className?: string;
  inputClassName?: string;
  style?: React.CSSProperties;
  /** Pair with a `<label htmlFor>`. Callers without a visible label pass `ariaLabel`. */
  id?: string;
  ariaLabel?: string;
}

/**
 * The one license `<select>`. Three call sites need the same dropdown with a
 * different empty-option label: dataset defaults ("Not recorded"), per-image
 * overrides ("Inherit from dataset") and the bulk modal ("— choose —").
 *
 * Picking "Other (free text)…" stores the bare `OTHER_PREFIX`, which reveals the
 * text input; typing stores `other:<text>`. That keeps the whole widget stateless
 * — an existing `other:<text>` value selects the same option on the way back in.
 * Note a bare `other:` normalises to "" server-side (backend/licenses.py), so
 * callers that guard against accidentally clearing a field must treat it as blank
 * (see `isBlankLicense` in constants/licenses.ts).
 */
export default function LicenseSelect({
  value, onChange, emptyLabel, disabled, className, inputClassName, style, id, ariaLabel,
}: Props) {
  const isOther = value.toLowerCase().startsWith(OTHER_PREFIX);
  const freeText = isOther ? value.slice(OTHER_PREFIX.length) : "";

  return (
    <>
      <select
        id={id}
        aria-label={ariaLabel}
        value={isOther ? OTHER_PREFIX : value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className={className}
        style={style}
      >
        <option value="">{emptyLabel}</option>
        {LICENSE_OPTIONS.map((l) => (
          <option key={l.id} value={l.id}>{l.label}</option>
        ))}
        <option value={OTHER_PREFIX}>Other (free text)…</option>
      </select>
      {isOther && (
        <input
          value={freeText}
          onChange={(e) => onChange(OTHER_PREFIX + e.target.value)}
          disabled={disabled}
          // Image.license / Dataset.license are String(64); "other:" eats 6.
          maxLength={58}
          placeholder="License name or terms"
          className={inputClassName ?? className}
          style={{ ...style, marginTop: 4 }}
          aria-label="Custom license"
        />
      )}
    </>
  );
}

