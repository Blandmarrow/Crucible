import { useState } from "react";
import { BLUEPRINT_LIMITS } from "./blueprintKeys";

/** A name input that holds its text while you type and commits on blur or Enter,
    so renaming a key never rebuilds the record on every keystroke. A rejected name
    (empty, or taken) reverts. */
export default function BlueprintNameField({
  value, onCommit, placeholder, label,
}: {
  value: string;
  /** Return false to refuse the name; the field then reverts. */
  onCommit: (name: string) => boolean;
  placeholder: string;
  label: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  function commit() {
    if (draft === null) return;
    const name = draft.trim();
    setDraft(null);
    if (name && name !== value) onCommit(name);
  }
  return (
    <input
      className="input mono" style={{ width: 180, height: 30, fontSize: 12 }}
      aria-label={label} maxLength={BLUEPRINT_LIMITS.name} placeholder={placeholder}
      value={draft ?? value}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setDraft(null);
      }}
    />
  );
}
