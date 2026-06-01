import { useId } from "react";
import { DELIMITER_PRESETS, type DelimiterMode } from "../../api/captioning";

interface Props {
  mode: DelimiterMode;
  delimiterParts: string[];
  onChange: (mode: DelimiterMode, parts: string[]) => void;
}

function displayPart(value: string): string {
  if (value === "\n") return "\\n";
  if (value === " ") return "·";
  return value;
}

export default function DelimiterControls({ mode, delimiterParts, onChange }: Props) {
  const radioName = useId();

  function togglePart(value: string) {
    const next = delimiterParts.includes(value)
      ? delimiterParts.filter((p) => p !== value)
      : [...delimiterParts, value];
    onChange(mode, next);
  }

  const preview = delimiterParts.map(displayPart).join("");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {(["overwrite", "append", "prepend"] as DelimiterMode[]).map((m) => (
          <label key={m} style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 13 }}>
            <input
              type="radio"
              name={radioName}
              value={m}
              checked={mode === m}
              onChange={() => onChange(m, delimiterParts)}
            />
            {m.charAt(0).toUpperCase() + m.slice(1)}
          </label>
        ))}
      </div>
      {mode !== "overwrite" && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: "var(--fg)", opacity: 0.6, whiteSpace: "nowrap" }}>Delimiter:</span>
          {DELIMITER_PRESETS.map((p) => {
            const active = delimiterParts.includes(p.value);
            return (
              <button
                key={p.value}
                type="button"
                onClick={() => togglePart(p.value)}
                className={`btn sm${active ? " primary" : " ghost"}`}
                style={{ fontFamily: "monospace", minWidth: 48 }}
              >
                {displayPart(p.value)} <span style={{ fontFamily: "sans-serif", fontSize: 10, opacity: 0.7 }}>{p.label}</span>
              </button>
            );
          })}
          {delimiterParts.length > 0 && (
            <span style={{ fontSize: 11.5, color: "var(--fg)", opacity: 0.5 }}>
              → <span style={{ fontFamily: "monospace" }}>{preview}</span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}
