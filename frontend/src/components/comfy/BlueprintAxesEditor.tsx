import type { BlueprintEntity, BlueprintSpec } from "../../api/comfy";
import BlueprintNameField from "./BlueprintNameField";
import { freshName } from "./blueprintKeys";

function newEntityId(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

interface Props {
  axes: BlueprintSpec["axes"];
  onChange: (axes: BlueprintSpec["axes"]) => void;
  /** Rename an axis and every scenario reference to it. */
  onRename: (from: string, to: string) => void;
}

export default function BlueprintAxesEditor({ axes, onChange, onRename }: Props) {
  const names = Object.keys(axes);

  function setEntities(axis: string, entities: BlueprintEntity[]) {
    onChange({ ...axes, [axis]: entities });
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-h">
        <h3>Axes</h3>
        <div style={{ flex: 1 }} />
        <button className="btn ghost sm"
          onClick={() => onChange({ ...axes, [freshName(axes, "axis")]: [] })}>+ Add axis</button>
      </div>
      <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {names.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            An axis is a kind of thing you vary, such as characters or biomes. Each entity on it
            becomes a folder, or part of one.
          </p>
        )}
        {names.map((axis) => (
          <div key={axis} data-testid={`axis-${axis}`}
            style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <BlueprintNameField
                value={axis} label="Axis name" placeholder="letters, digits, _"
                onCommit={(n) => {
                  if (n in axes) return false;
                  onRename(axis, n);
                  return true;
                }}
              />
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>
                {axes[axis].length} entit{axes[axis].length === 1 ? "y" : "ies"}
              </span>
              <div style={{ flex: 1 }} />
              <button className="btn ghost sm"
                onClick={() => setEntities(axis, [...axes[axis], { id: newEntityId(), label: "", descriptor: "" }])}>
                + Add entity
              </button>
              <button className="btn ghost sm" style={{ color: "var(--bad)" }} aria-label={`Remove axis ${axis}`}
                onClick={() => {
                  const next = { ...axes };
                  delete next[axis];
                  onChange(next);
                }}>Remove axis</button>
            </div>
            {axes[axis].map((ent, i) => (
              <div key={ent.id} style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 6 }}>
                <input
                  className="input" style={{ width: 170, height: 30, fontSize: 12 }}
                  aria-label={`${axis} entity label`} placeholder="Label (names the folder)"
                  value={ent.label}
                  onChange={(e) => setEntities(axis, axes[axis].map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))}
                />
                <textarea
                  className="input" style={{ flex: 1, minHeight: 30, height: 30, fontSize: 12, resize: "vertical" }}
                  aria-label={`${axis} entity descriptor`} placeholder="Descriptor (goes into the prompt)"
                  value={ent.descriptor}
                  onChange={(e) => setEntities(axis, axes[axis].map((x, j) => (j === i ? { ...x, descriptor: e.target.value } : x)))}
                />
                <button className="btn ghost sm" aria-label={`Remove ${ent.label || "entity"}`}
                  onClick={() => setEntities(axis, axes[axis].filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
