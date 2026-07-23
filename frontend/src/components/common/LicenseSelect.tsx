import { FIELD_MAX_LEN, LICENSE_OPTIONS, OTHER_PREFIX } from "../../constants/licenses";

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
  /** `other:` licenses already used in this dataset, offered as their own options
   *  (see hooks/useCustomLicenses). Empty/omitted keeps the vocabulary-only list. */
  customOptions?: string[];
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
 *
 * `customOptions` adds the free-text licenses already in use in the dataset as
 * real options, so one can be picked instead of retyped (a typo there makes a
 * second, near-duplicate license bucket). Picking one still reveals the text
 * input, pre-filled: editing it off the list simply falls back to the "Other
 * (free text)…" option, so nothing is trapped.
 */
export default function LicenseSelect({
  value, onChange, emptyLabel, disabled, className, inputClassName, style, id, ariaLabel,
  customOptions,
}: Props) {
  const isOther = value.toLowerCase().startsWith(OTHER_PREFIX);
  const freeText = isOther ? value.slice(OTHER_PREFIX.length) : "";
  const customs = customOptions ?? [];
  // An in-use value selects its own option; anything else typed falls through to
  // the generic free-text option so the `<select>` always mirrors `value`.
  const selectValue = isOther ? (customs.includes(value) ? value : OTHER_PREFIX) : value;

  return (
    <>
      <select
        id={id}
        aria-label={ariaLabel}
        value={selectValue}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className={className}
        style={style}
      >
        <option value="">{emptyLabel}</option>
        {LICENSE_OPTIONS.map((l) => (
          <option key={l.id} value={l.id}>{l.label}</option>
        ))}
        {customs.length > 0 && (
          <optgroup label="Used in this dataset">
            {customs.map((lic) => (
              <option key={lic} value={lic}>{lic.slice(OTHER_PREFIX.length)}</option>
            ))}
          </optgroup>
        )}
        <option value={OTHER_PREFIX}>Other (free text)…</option>
      </select>
      {isOther && (
        <input
          value={freeText}
          onChange={(e) => onChange(OTHER_PREFIX + e.target.value)}
          disabled={disabled}
          // The column holds the *normalized* value, and "other:" eats 6 of it.
          maxLength={FIELD_MAX_LEN.license - OTHER_PREFIX.length}
          placeholder="License name or terms"
          className={inputClassName ?? className}
          style={{ ...style, marginTop: 4 }}
          aria-label="Custom license"
        />
      )}
    </>
  );
}

