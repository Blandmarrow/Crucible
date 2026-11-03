import { useState } from "react";
import type { BlueprintSpec } from "../../api/comfy";
import BlueprintNameField from "./BlueprintNameField";
import { freshName } from "./blueprintKeys";

/** Holds its own text so a trailing newline survives while you type; the spec only
    ever gets the non-empty lines. */
function PiecesField({ name, pieces, onChange }: { name: string; pieces: string[]; onChange: (p: string[]) => void }) {
  const [text, setText] = useState(pieces.join("\n"));
  return (
    <textarea
      className="input" style={{ width: "100%", minHeight: 70, fontSize: 12, resize: "vertical" }}
      aria-label={`${name} pieces`} placeholder={"One piece per line\nlow angle\nwide shot"}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(e.target.value.split("\n").map((l) => l.trim()).filter(Boolean));
      }}
    />
  );
}

interface Props {
  variations: BlueprintSpec["variations"];
  onChange: (v: BlueprintSpec["variations"]) => void;
  /** Rename a variation and every template reference to it. */
  onRename: (from: string, to: string) => void;
}

export default function BlueprintVariationsEditor({ variations, onChange, onRename }: Props) {
  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-h">
        <h3>Variations</h3>
        <div style={{ flex: 1 }} />
        <button className="btn ghost sm"
          onClick={() => onChange({ ...variations, [freshName(variations, "variation")]: [] })}>+ Add variation</button>
      </div>
      <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {Object.keys(variations).length === 0 && (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            A variation is a list of phrases, such as camera angles. Each prompt picks one without
            changing the folder.
          </p>
        )}
        {Object.keys(variations).map((name) => (
          <div key={name} data-testid={`variation-${name}`}
            style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <BlueprintNameField
                value={name} label="Variation name" placeholder="letters, digits, _"
                onCommit={(n) => {
                  if (n in variations) return false;
                  onRename(name, n);
                  return true;
                }}
              />
              <div style={{ flex: 1 }} />
              <button className="btn ghost sm" style={{ color: "var(--bad)" }} aria-label={`Remove variation ${name}`}
                onClick={() => {
                  const next = { ...variations };
                  delete next[name];
                  onChange(next);
                }}>Remove variation</button>
            </div>
            <PiecesField name={name} pieces={variations[name]} onChange={(p) => onChange({ ...variations, [name]: p })} />
          </div>
        ))}
      </div>
    </div>
  );
}
