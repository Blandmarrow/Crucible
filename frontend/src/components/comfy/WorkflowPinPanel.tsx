import { useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import type { ComfyPlan, PinnedParam } from "../../api/comfy";

interface Props {
  plan: ComfyPlan;
  onSave: (patch: Partial<Pick<ComfyPlan, "workflow_json" | "pinned_params" | "seed_mode">>) => void;
  saving: boolean;
}

type WorkflowJson = ComfyPlan["workflow_json"];

function isApiFormat(obj: unknown): obj is WorkflowJson {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return false;
  const entries = Object.values(obj as Record<string, unknown>);
  if (entries.length === 0) return false;
  return entries.every(
    (n) => !!n && typeof n === "object" && "class_type" in (n as object) && "inputs" in (n as object)
  );
}

/** Scalar inputs only — array values like ["6", 0] are node connections, not parameters. */
function scalarInputs(inputs: Record<string, unknown>): [string, string | number | boolean][] {
  return Object.entries(inputs).filter(
    (e): e is [string, string | number | boolean] =>
      ["string", "number", "boolean"].includes(typeof e[1])
  );
}

export default function WorkflowPinPanel({ plan, onSave, saving }: Props) {
  const [workflowText, setWorkflowText] = useState("");
  const [pins, setPins] = useState<PinnedParam[]>(plan.pinned_params);
  const [seedMode, setSeedMode] = useState(plan.seed_mode);
  const [parseError, setParseError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // The workflow being edited: freshly pasted text takes precedence over the saved one.
  const workflow: WorkflowJson | null = useMemo(() => {
    if (workflowText.trim()) {
      try {
        const parsed = JSON.parse(workflowText);
        if (parsed && typeof parsed === "object" && ("nodes" in parsed || "links" in parsed)) {
          return null; // UI-format export — flagged below
        }
        return isApiFormat(parsed) ? parsed : null;
      } catch {
        return null;
      }
    }
    return Object.keys(plan.workflow_json).length > 0 ? plan.workflow_json : null;
  }, [workflowText, plan.workflow_json]);

  function handleTextChange(text: string) {
    setWorkflowText(text);
    if (!text.trim()) { setParseError(null); return; }
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && ("nodes" in parsed || "links" in parsed)) {
        setParseError('This looks like a UI-format export. In ComfyUI use "Workflow → Export (API)" (or enable dev mode → "Save (API Format)") and paste that JSON instead.');
      } else if (!isApiFormat(parsed)) {
        setParseError("Not an API-format workflow — expected a JSON object of {node_id: {class_type, inputs}}.");
      } else {
        setParseError(null);
      }
    } catch (e) {
      setParseError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  function loadFile(f: File) {
    f.text().then(handleTextChange).catch(() => toast.error("Could not read file"));
  }

  const pinKey = (nodeId: string, input: string) => `${nodeId}::${input}`;
  const pinnedSet = useMemo(() => new Set(pins.map((p) => pinKey(p.node_id, p.input))), [pins]);

  function togglePin(nodeId: string, input: string) {
    if (pinnedSet.has(pinKey(nodeId, input))) {
      setPins(pins.filter((p) => !(p.node_id === nodeId && p.input === input)));
    } else {
      // Default alias: the input name, deduped with the node id if taken.
      const taken = new Set(pins.map((p) => p.alias));
      const alias = taken.has(input) ? `${input}_${nodeId}` : input;
      setPins([...pins, { node_id: nodeId, input, alias, is_prompt: pins.length === 0 && input === "text" }]);
    }
  }

  function patchPin(idx: number, patch: Partial<PinnedParam>) {
    setPins(pins.map((p, i) => {
      if (patch.is_prompt) return { ...p, ...(i === idx ? patch : { is_prompt: false }) };
      return i === idx ? { ...p, ...patch } : p;
    }));
  }

  function handleSave() {
    const aliases = pins.map((p) => p.alias.trim());
    if (aliases.some((a) => !a)) { toast.error("Every pinned parameter needs an alias"); return; }
    if (new Set(aliases).size !== aliases.length) { toast.error("Pinned aliases must be unique"); return; }
    const patch: Parameters<typeof onSave>[0] = { pinned_params: pins, seed_mode: seedMode };
    if (workflowText.trim()) {
      if (!workflow) { toast.error("Fix the workflow JSON before saving"); return; }
      patch.workflow_json = workflow;
    }
    onSave(patch);
    setWorkflowText("");
  }

  const hasSavedWorkflow = Object.keys(plan.workflow_json).length > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Workflow JSON */}
      <div className="panel">
        <div className="panel-h">
          <h3>Workflow template</h3>
          <div style={{ flex: 1 }} />
          {hasSavedWorkflow && !workflowText.trim() && (
            <span className="badge dot good">Workflow saved · {Object.keys(plan.workflow_json).length} nodes</span>
          )}
          <button className="btn ghost sm" onClick={() => fileRef.current?.click()}>Load .json file</button>
          <input
            ref={fileRef} type="file" accept=".json,application/json" style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) loadFile(f); e.target.value = ""; }}
          />
        </div>
        <div className="panel-b">
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 8px" }}>
            Paste an <b>API-format</b> workflow (ComfyUI: Workflow → Export (API)). Pinned inputs below become
            per-row columns; everything else runs exactly as saved in the template.
          </p>
          <textarea
            className="input mono"
            style={{ width: "100%", minHeight: 100, fontSize: 11, resize: "vertical" }}
            placeholder={hasSavedWorkflow ? "Paste new JSON here to replace the saved workflow…" : '{"3": {"class_type": "KSampler", "inputs": {...}}, ...}'}
            value={workflowText}
            onChange={(e) => handleTextChange(e.target.value)}
          />
          {parseError && <p style={{ color: "var(--bad)", fontSize: 12, marginTop: 6 }}>{parseError}</p>}
        </div>
      </div>

      {/* Pinned params */}
      <div className="panel">
        <div className="panel-h">
          <h3>Pinned parameters</h3>
          <div style={{ flex: 1 }} />
          <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", alignItems: "center", gap: 6 }}>
            Seed mode
            <select className="select" style={{ height: 28, fontSize: 12 }} value={seedMode}
              onChange={(e) => setSeedMode(e.target.value as ComfyPlan["seed_mode"])}
              title='Applies to a pin aliased "seed" when a row leaves it blank'>
              <option value="fixed">Fixed (template value)</option>
              <option value="random">Random per row</option>
              <option value="increment">Increment per row</option>
            </select>
          </label>
          <button className="btn primary sm" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
        <div className="panel-b">
          {pins.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
              No parameters pinned yet — pin inputs from the node list below. Mark one as the <b>prompt</b> to
              enable bulk prompt paste and prompt-as-caption.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {pins.map((p, i) => (
                <div key={pinKey(p.node_id, p.input)} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="mono" style={{ fontSize: 11, color: "var(--fg-dim)", width: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    node {p.node_id} · {p.input}
                  </span>
                  <input
                    className="input" style={{ width: 160, fontSize: 12 }} value={p.alias}
                    onChange={(e) => patchPin(i, { alias: e.target.value })} placeholder="alias"
                  />
                  <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                    <input type="radio" name="is-prompt-pin" checked={p.is_prompt} onChange={() => patchPin(i, { is_prompt: true })} />
                    prompt
                  </label>
                  <button className="icon-btn" title="Unpin" onClick={() => togglePin(p.node_id, p.input)}>×</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Node list */}
      {workflow && (
        <div className="panel">
          <div className="panel-h"><h3>Workflow nodes</h3></div>
          <div className="panel-b" style={{ maxHeight: "45vh", overflowY: "auto" }}>
            {Object.entries(workflow).map(([nodeId, node]) => {
              const inputs = scalarInputs(node.inputs ?? {});
              if (inputs.length === 0) return null;
              return (
                <div key={nodeId} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                    <span className="mono" style={{ color: "var(--fg-dim)" }}>{nodeId}</span>{" "}
                    {node.class_type}
                    {node._meta?.title && node._meta.title !== node.class_type && (
                      <span style={{ color: "var(--fg-mute)", fontWeight: 400 }}> — {node._meta.title}</span>
                    )}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    {inputs.map(([input, value]) => {
                      const pinned = pinnedSet.has(pinKey(nodeId, input));
                      return (
                        <div key={input} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, padding: "2px 0" }}>
                          <button
                            className={`btn sm${pinned ? " primary" : " ghost"}`}
                            style={{ minWidth: 52, padding: "1px 8px", fontSize: 11 }}
                            onClick={() => togglePin(nodeId, input)}
                          >
                            {pinned ? "Pinned" : "Pin"}
                          </button>
                          <span className="mono" style={{ color: "var(--fg-mute)", width: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{input}</span>
                          <span className="mono" style={{ color: "var(--fg-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 420 }}>
                            {String(value)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
