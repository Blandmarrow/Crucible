import { useState } from "react";

interface Props {
  /** The currently bound key, or null. */
  value: string | null;
  /** Called with the new key, or null to clear it. */
  onChange: (key: string | null) => void;
  /** The label that already owns the pressed key, if any — shown as a warning
   *  so a collision is pre-empted in the UI rather than only as a server 409. */
  ownerOf: (key: string) => string | null;
  disabled?: boolean;
}

/**
 * Captures the next keypress while focused and binds it as a label hotkey.
 *
 * The accepted charset is `[a-z0-9]` and nothing else. That is what makes
 * conflict prevention **structural rather than a blocklist**: the charset cannot
 * express Escape, Space, ArrowLeft/Right or Delete — the five keys
 * `ImageDetailPage` already binds — so no `RESERVED = new Set([...])` is needed,
 * and none can go stale when a sixth binding is added. The instinct to add one
 * is the version that rots.
 *
 * While capturing, every keydown is `preventDefault`ed and `stopPropagation`ed
 * so nothing behind the button sees it: Escape cancels, Backspace/Delete clears
 * (sending `null`, which the PATCH honours because it uses `exclude_unset`), and
 * modifier chords are ignored.
 */
export default function HotkeyCaptureButton({ value, onChange, ownerOf, disabled }: Props) {
  const [capturing, setCapturing] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);

  function onKeyDown(e: React.KeyboardEvent<HTMLButtonElement>) {
    if (!capturing) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") {
      setCapturing(false);
      setConflict(null);
      return;
    }
    if (e.key === "Backspace" || e.key === "Delete") {
      setCapturing(false);
      setConflict(null);
      onChange(null);
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (!/^[a-z0-9]$/i.test(e.key)) return;
    const key = e.key.toLowerCase();
    const owner = ownerOf(key);
    if (owner) {
      setConflict(owner);
      return;
    }
    setCapturing(false);
    setConflict(null);
    onChange(key);
  }

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <button
        type="button"
        className="btn-ghost btn-sm"
        disabled={disabled}
        aria-label={value ? `Hotkey ${value}` : "Set hotkey"}
        onClick={() => {
          setConflict(null);
          setCapturing((c) => !c);
        }}
        onBlur={() => {
          setCapturing(false);
          setConflict(null);
        }}
        onKeyDown={onKeyDown}
        style={{ minWidth: 64, fontFamily: capturing ? undefined : "var(--font-mono, monospace)" }}
      >
        {capturing ? "Press a key…" : value ? value : "Set key"}
      </button>
      {value && !capturing && (
        <button
          type="button"
          className="btn-ghost btn-sm"
          disabled={disabled}
          aria-label="Clear hotkey"
          onClick={() => onChange(null)}
        >
          ✕
        </button>
      )}
      {conflict && (
        <span style={{ fontSize: 11.5, color: "var(--danger, #f87171)" }}>
          Used by “{conflict}”
        </span>
      )}
    </span>
  );
}
