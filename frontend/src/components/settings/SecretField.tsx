import { useId, useState } from "react";
import type { Secret } from "../../api/settings";

interface Props {
  label: string;
  /** What this key unlocks, shown under the label. */
  help: React.ReactNode;
  secret: Secret;
  /** The .env / OS environment variable this secret falls back to, e.g. "HF_TOKEN". */
  envVar: string;
  placeholder?: string;
  busy?: boolean;
  onSave: (value: string) => void;
  onClear: () => void;
}

/**
 * One password row in Settings -> API Keys: draft input, a status line naming the source of
 * the effective value, and separate Save / Clear buttons.
 *
 * Save and Clear are never overloaded onto one control. Blank means "keep the current value"
 * everywhere else in this app (the LLM provider form does exactly that), so Save is disabled
 * on an empty draft and clearing gets its own button, which sends an explicit "".
 *
 * The row is addressable by accessible name from the outside: `useId` + `<label htmlFor>`
 * for the input (the `ProvenanceFields` pattern), and `role="group"` + `aria-label` on the
 * root so a test can scope to one row's Save/Clear without walking the DOM — three rows
 * carry identically-named buttons.
 */
export default function SecretField({
  label, help, secret, envVar, placeholder, busy, onSave, onClear,
}: Props) {
  const [draft, setDraft] = useState("");
  // Unique per instance: three of these are mounted at once.
  const uid = useId();

  function save() {
    const value = draft.trim();
    if (!value) return;
    onSave(value);
    setDraft("");
  }

  const status =
    secret.source === "db" ? (
      <>Saved here · <span className="mono">{secret.masked}</span></>
    ) : secret.source === "env" ? (
      <>Inherited from .env (<span className="mono">{envVar}</span>) · <span className="mono">{secret.masked}</span></>
    ) : (
      <>Not set</>
    );

  return (
    <div role="group" aria-label={label}>
      <label
        htmlFor={`${uid}-secret`}
        style={{ display: "block", fontWeight: 500, fontSize: 13, marginBottom: 6 }}
      >
        {label}
      </label>
      <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>{help}</p>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input
          id={`${uid}-secret`}
          className="input"
          type="password"
          // Not "off": Chrome ignores that on a password input and offers to save the key
          // to the browser's password manager. "new-password" is the value it honours.
          autoComplete="new-password"
          placeholder={placeholder ?? "Leave blank to keep the current value"}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") save(); }}
          style={{ flex: 1 }}
        />
        <button className="btn primary sm" onClick={save} disabled={busy || !draft.trim()}>
          Save
        </button>
        {secret.source === "db" && (
          <button className="btn ghost sm" onClick={onClear} disabled={busy}>
            Clear
          </button>
        )}
      </div>
      <div
        style={{
          fontSize: 11.5,
          marginTop: 6,
          color: secret.source === "db" ? "var(--fg)" : "var(--fg-dim)",
        }}
      >
        {status}
      </div>
    </div>
  );
}
