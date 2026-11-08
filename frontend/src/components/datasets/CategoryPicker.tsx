import { useState, useId } from "react";

// ── CategoryPicker ────────────────────────────────────────────────────────────
// Select from existing categories or type a new one.
interface CategoryPickerProps {
  value: string;
  onChange: (v: string) => void;
  existingCategories: string[];
  label?: string;
  labelNote?: string;
  autoFocusNew?: boolean;
}
export default function CategoryPicker({ value, onChange, existingCategories, label, labelNote, autoFocusNew }: CategoryPickerProps) {
  // "new" mode: user explicitly chose "New category…" from the dropdown.
  // CategoryPicker is always inside a conditionally-rendered modal, so it remounts
  // on each open — no need for a sync effect; useState init is sufficient.
  const inExisting = value === "" || existingCategories.includes(value);
  const [isNew, setIsNew] = useState(!inExisting);

  const selectValue = isNew ? "__new__" : value;
  // Unique per instance: the create and edit modals each render one.
  const selectId = useId();

  const handleSelect = (v: string) => {
    if (v === "__new__") {
      setIsNew(true);
      onChange(""); // clear; user will type
    } else {
      setIsNew(false);
      onChange(v);
    }
  };

  return (
    <div>
      {label && (
        <label className="label" htmlFor={selectId}>
          {label}
          {labelNote && <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}> {labelNote}</span>}
        </label>
      )}
      <select
        id={selectId}
        className="select"
        value={selectValue}
        onChange={(e) => handleSelect(e.target.value)}
        style={{ width: "100%" }}
      >
        <option value="">(None)</option>
        {existingCategories.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
        <option value="__new__">New category…</option>
      </select>
      {isNew && (
        <input
          className="input"
          placeholder="Category name"
          aria-label="New category name"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus={autoFocusNew}
          style={{ marginTop: 8, width: "100%" }}
        />
      )}
    </div>
  );
}
