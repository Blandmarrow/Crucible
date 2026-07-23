import { useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import { coerceCellValue, comfyApi } from "../../api/comfy";
import type { CanvasWorkflowResponse, ComfyPlan, PinnedParam } from "../../api/comfy";
import SyncCanvasModal from "./SyncCanvasModal";
import WorkflowScanModal from "./WorkflowScanModal";

interface Props {
  plan: ComfyPlan;
  onSave: (patch: Partial<Pick<ComfyPlan, "workflow_json" | "pinned_params" | "output_node_ids" | "output_is_synthetic">>) => void;
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

function nodeLabel(wf: WorkflowJson, nodeId: string): string {
  const n = wf[nodeId];
  if (!n) return `#${nodeId}`;
  const title = n._meta?.title && n._meta.title !== n.class_type ? ` — ${n._meta.title}` : "";
  return `#${nodeId} ${n.class_type}${title}`;
}

const INT_MODE_LABEL: Record<string, string> = {
  fixed: "Fixed",
  random: "Random per row",
  increment: "+1 Increment per row",
};

export default function WorkflowPinPanel({ plan, onSave, saving }: Props) {
  const [workflowText, setWorkflowText] = useState("");
  const [pins, setPins] = useState<PinnedParam[]>(plan.pinned_params);
  const [outputNodeIds, setOutputNodeIds] = useState<string[]>(plan.output_node_ids);
  const [outputIsSynthetic, setOutputIsSynthetic] = useState<boolean>(plan.output_is_synthetic);
  const [parseError, setParseError] = useState<string | null>(null);
  const [nodeFilter, setNodeFilter] = useState("");
  const [showScan, setShowScan] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncSnapshot, setSyncSnapshot] = useState<CanvasWorkflowResponse | null>(null);
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

  function templateVal(pin: Pick<PinnedParam, "node_id" | "input">): unknown {
    return workflow?.[pin.node_id]?.inputs?.[pin.input];
  }

  function makePin(nodeId: string, input: string, taken: Set<string>, autoPrompt: boolean): PinnedParam {
    const alias = taken.has(input) ? `${input}_${nodeId}` : input;
    taken.add(alias);
    const isPrompt =
      autoPrompt && input === "text" && !pins.some((p) => p.is_prompt) &&
      typeof workflow?.[nodeId]?.inputs?.[input] === "string";
    return { node_id: nodeId, input, alias, is_prompt: isPrompt, per_row: isPrompt, value: null, int_mode: null };
  }

  function togglePin(nodeId: string, input: string) {
    if (pinnedSet.has(pinKey(nodeId, input))) {
      setPins(pins.filter((p) => !(p.node_id === nodeId && p.input === input)));
    } else {
      const taken = new Set(pins.map((p) => p.alias));
      setPins([...pins, makePin(nodeId, input, taken, true)]);
    }
  }

  function pinNode(nodeId: string) {
    const inputs = scalarInputs(workflow?.[nodeId]?.inputs ?? {});
    const taken = new Set(pins.map((p) => p.alias));
    const added = inputs
      .filter(([input]) => !pinnedSet.has(pinKey(nodeId, input)))
      .map(([input]) => makePin(nodeId, input, taken, false));
    if (added.length) setPins([...pins, ...added]);
  }

  function unpinNode(nodeId: string) {
    setPins(pins.filter((p) => p.node_id !== nodeId));
  }

  function patchPin(idx: number, patch: Partial<PinnedParam>) {
    setPins(pins.map((p, i) => {
      if (patch.is_prompt) {
        // Single prompt pin; the prompt is always a per-row column.
        if (i === idx) return { ...p, ...patch, per_row: true };
        return { ...p, is_prompt: false };
      }
      return i === idx ? { ...p, ...patch } : p;
    }));
  }

  function commitPinValue(idx: number, raw: string) {
    const pin = pins[idx];
    const tv = templateVal(pin);
    const value: PinnedParam["value"] = raw === "" ? null : coerceCellValue(raw, typeof tv === "number");
    patchPin(idx, { value });
  }

  function validateAliases(list: PinnedParam[]): boolean {
    const aliases = list.map((p) => p.alias.trim());
    if (aliases.some((a) => !a)) { toast.error("Every pinned parameter needs an alias"); return false; }
    if (new Set(aliases).size !== aliases.length) { toast.error("Pinned aliases must be unique"); return false; }
    return true;
  }

  function handleSave() {
    if (!validateAliases(pins)) return;
    const patch: Parameters<typeof onSave>[0] = {
      pinned_params: pins, output_node_ids: outputNodeIds, output_is_synthetic: outputIsSynthetic,
    };
    if (workflowText.trim()) {
      if (!workflow) { toast.error("Fix the workflow JSON before saving"); return; }
      patch.workflow_json = workflow;
    }
    onSave(patch);
    setWorkflowText("");
  }

  async function handleSync() {
    setSyncing(true);
    try {
      setSyncSnapshot(await comfyApi.canvasWorkflow());
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? "Could not pull a workflow from ComfyUI");
    } finally {
      setSyncing(false);
    }
  }

  /** Same resolution check the backend's patch_workflow performs per pin. */
  function pinResolves(pin: PinnedParam, wf: WorkflowJson): boolean {
    return pin.node_id in wf && pin.input in (wf[pin.node_id].inputs ?? {});
  }

  // Commits immediately (one PATCH: new workflow + surviving pins + current
  // panel state) — the sync dialog is the confirmation, no second Save step.
  function applySync(snapshot: CanvasWorkflowResponse) {
    const kept = pins.filter((p) => pinResolves(p, snapshot.workflow));
    if (!validateAliases(kept)) return;
    onSave({
      workflow_json: snapshot.workflow, pinned_params: kept,
      output_node_ids: outputNodeIds, output_is_synthetic: outputIsSynthetic,
    });
    setPins(kept);
    setWorkflowText("");
    setSyncSnapshot(null);
    const dropped = pins.length - kept.length;
    toast.success(
      `Synced ${snapshot.name ?? (snapshot.source === "history" ? "last queued workflow" : "canvas")}` +
      (dropped ? ` — ${dropped} pin${dropped > 1 ? "s" : ""} dropped` : "")
    );
  }

  const hasSavedWorkflow = Object.keys(plan.workflow_json).length > 0;
  // Without any output-node selection, runs only import type=="output" images,
  // which only Save-type nodes produce.
  const hasSaveNode = useMemo(
    () => !workflow || Object.values(workflow).some((n) => /save/i.test(n.class_type)),
    [workflow]
  );
  const missingOutputNodes = workflow ? outputNodeIds.filter((id) => !(id in workflow)) : [];

  const filteredNodes = useMemo(() => {
    if (!workflow) return [];
    const q = nodeFilter.trim().toLowerCase();
    if (!q) return Object.entries(workflow);
    return Object.entries(workflow).filter(([nodeId, node]) => {
      const inputs = scalarInputs(node.inputs ?? {});
      return (
        nodeId.toLowerCase().includes(q) ||
        node.class_type.toLowerCase().includes(q) ||
        (node._meta?.title ?? "").toLowerCase().includes(q) ||
        inputs.some(([input, value]) => input.toLowerCase().includes(q) || String(value).toLowerCase().includes(q))
      );
    });
  }, [workflow, nodeFilter]);

  // Output-node candidates: Save/Preview-type nodes as toggle chips, rest via the add-select.
  const outputCandidates = useMemo(() => {
    if (!workflow) return [];
    return Object.entries(workflow)
      .filter(([, node]) => /save|preview/i.test(node.class_type))
      .map(([nodeId]) => nodeId);
  }, [workflow]);
  const otherNodes = useMemo(() => {
    if (!workflow) return [];
    return Object.keys(workflow).filter((id) => !outputCandidates.includes(id) && !outputNodeIds.includes(id));
  }, [workflow, outputCandidates, outputNodeIds]);

  function toggleOutputNode(nodeId: string) {
    setOutputNodeIds((prev) => prev.includes(nodeId) ? prev.filter((i) => i !== nodeId) : [...prev, nodeId]);
  }

  // Pinned groups: node order = first appearance in pins.
  const groups = useMemo(() => {
    const order: string[] = [];
    const byNode = new Map<string, { pin: PinnedParam; idx: number }[]>();
    pins.forEach((pin, idx) => {
      if (!byNode.has(pin.node_id)) { byNode.set(pin.node_id, []); order.push(pin.node_id); }
      byNode.get(pin.node_id)!.push({ pin, idx });
    });
    return order.map((nodeId) => ({ nodeId, items: byNode.get(nodeId)! }));
  }, [pins]);

  const chipStyle: React.CSSProperties = {
    fontSize: 11, fontFamily: "var(--font-mono, monospace)", color: "var(--fg-dim)",
    background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 4, padding: "0 6px",
    whiteSpace: "nowrap",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Workflow JSON + output nodes */}
      <div className="panel">
        <div className="panel-h">
          <h3>Workflow template</h3>
          <div style={{ flex: 1 }} />
          {hasSavedWorkflow && !workflowText.trim() && (
            <span className="badge dot good">Workflow saved · {Object.keys(plan.workflow_json).length} nodes</span>
          )}
          <button
            className="btn ghost sm"
            disabled={syncing}
            title="Pull the workflow from ComfyUI: the open canvas (CrucibleBridge extension) or the last-queued prompt"
            onClick={handleSync}
          >
            {syncing ? "Syncing…" : "Sync from canvas"}
          </button>
          <button className="btn ghost sm" onClick={() => setShowScan(true)}>Scan folder…</button>
          <button className="btn ghost sm" onClick={() => fileRef.current?.click()}>Load .json file</button>
          <input
            ref={fileRef} type="file" accept=".json,application/json" style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) loadFile(f); e.target.value = ""; }}
          />
        </div>
        <div className="panel-b">
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 8px" }}>
            Paste an <b>API-format</b> workflow (ComfyUI: Workflow → Export (API)). Pin inputs below to expose
            them in Crucible; everything else runs exactly as saved in the template.
          </p>
          <textarea
            className="input mono"
            style={{ width: "100%", minHeight: 100, fontSize: 11, resize: "vertical" }}
            placeholder={hasSavedWorkflow ? "Paste new JSON here to replace the saved workflow…" : '{"3": {"class_type": "KSampler", "inputs": {...}}, ...}'}
            value={workflowText}
            onChange={(e) => handleTextChange(e.target.value)}
          />
          {parseError && <p style={{ color: "var(--bad)", fontSize: 12, marginTop: 6 }}>{parseError}</p>}

          {workflow && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}
                title="Which nodes' images the run imports. None selected = everything a Save-type node writes; selecting nodes imports their images even if they are previews (PreviewImage).">
                Import images from
              </span>
              {outputNodeIds.length === 0 && (
                <span className="badge dot">Auto — SaveImage outputs</span>
              )}
              {[...outputCandidates, ...outputNodeIds.filter((id) => !outputCandidates.includes(id) && id in workflow)].map((nodeId) => {
                const on = outputNodeIds.includes(nodeId);
                return (
                  <button
                    key={nodeId}
                    className={`btn sm${on ? " primary" : " ghost"}`}
                    style={{ fontSize: 11, padding: "1px 8px" }}
                    onClick={() => toggleOutputNode(nodeId)}
                    title={on ? "Selected — click to remove" : "Click to import this node's images"}
                  >
                    {on ? "✓ " : ""}{nodeLabel(workflow, nodeId)}
                  </button>
                );
              })}
              {otherNodes.length > 0 && (
                <select
                  className="select" style={{ height: 26, fontSize: 11 }} value=""
                  onChange={(e) => { if (e.target.value) toggleOutputNode(e.target.value); }}
                >
                  <option value="">+ other node…</option>
                  {otherNodes.map((id) => <option key={id} value={id}>{nodeLabel(workflow, id)}</option>)}
                </select>
              )}
            </div>
          )}

          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <input
                type="checkbox"
                className="checkbox"
                checked={outputIsSynthetic}
                onChange={(e) => setOutputIsSynthetic(e.target.checked)}
              />
              Output is synthetic (self-created)
            </label>
            <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>
              {outputIsSynthetic
                ? "Imported images are recorded as ComfyUI / synthetic."
                : "Imported images inherit this dataset's source & license defaults — for a plan that derives from licensed material."}
            </span>
          </div>

          {!hasSaveNode && outputNodeIds.length === 0 && (
            <p style={{ color: "var(--warn, #d97706)", fontSize: 12, marginTop: 6 }}>
              ⚠ No Save node found — runs will fail because nothing produces output images. Add a
              SaveImage node in ComfyUI, or select your PreviewImage under “Import images from”.
            </p>
          )}
          {missingOutputNodes.length > 0 && (
            <p style={{ color: "var(--warn, #d97706)", fontSize: 12, marginTop: 6 }}>
              ⚠ Selected output node{missingOutputNodes.length > 1 ? "s" : ""} {missingOutputNodes.map((i) => `#${i}`).join(", ")} no
              longer exist{missingOutputNodes.length > 1 ? "" : "s"} in this workflow — review the workflow or the selection above.
            </p>
          )}
        </div>
      </div>

      {/* Pinned params, grouped by node */}
      <div className="panel">
        <div className="panel-h">
          <h3>Pinned parameters</h3>
          <div style={{ flex: 1 }} />
          <button className="btn primary sm" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
        <div className="panel-b">
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
            A pinned parameter is editable here as a <b>run default</b> (applies to every row). Switch{" "}
            <b>per row</b> on to give it a queue column instead — blank cells fall back to the default.
            Mark one text parameter as the <b>prompt</b>: that column receives pasted/imported prompts and
            feeds prompt-as-caption.
          </p>
          {groups.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
              Nothing pinned yet — pin single inputs or whole nodes from the list below.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {groups.map(({ nodeId, items }) => (
                <div key={nodeId} style={{ border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
                  <div style={{
                    display: "flex", alignItems: "center", gap: 8, padding: "5px 10px",
                    background: "var(--surface-2)", borderBottom: "1px solid var(--line)", fontSize: 12,
                  }}>
                    <span style={chipStyle}>{workflow ? nodeLabel(workflow, nodeId) : `#${nodeId}`}</span>
                    {workflow && !(nodeId in workflow) && (
                      <span style={{ color: "var(--warn, #d97706)", fontSize: 11 }}>not in workflow</span>
                    )}
                    <div style={{ flex: 1 }} />
                    <button className="btn ghost sm" style={{ fontSize: 11, padding: "0 8px" }} onClick={() => unpinNode(nodeId)}>
                      Unpin node ×
                    </button>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    {items.map(({ pin, idx }) => {
                      const tv = templateVal(pin);
                      const isInt = typeof tv === "number" && Number.isInteger(tv);
                      return (
                        <div key={pinKey(pin.node_id, pin.input)} style={{
                          display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
                          borderBottom: "1px solid var(--line-2, var(--line))", fontSize: 12,
                        }}>
                          <div style={{ width: 150, flexShrink: 0 }}>
                            <input
                              className="input" style={{ width: "100%", fontSize: 12, height: 24 }}
                              value={pin.alias}
                              onChange={(e) => patchPin(idx, { alias: e.target.value })} placeholder="alias"
                            />
                            <span className="mono" style={{ fontSize: 10, color: "var(--fg-dim)" }}>{pin.input}</span>
                          </div>
                          {typeof tv === "boolean" ? (
                            <select
                              className="select"
                              style={{ flex: 1, fontSize: 11.5, height: 26, minWidth: 120 }}
                              value={pin.value === true ? "true" : pin.value === false ? "false" : ""}
                              title="Run default — applies to every row that doesn't set its own value. Template = the workflow's own value."
                              onChange={(e) => patchPin(idx, { value: e.target.value === "" ? null : e.target.value === "true" })}
                            >
                              <option value="">template: {String(tv)}</option>
                              <option value="true">true</option>
                              <option value="false">false</option>
                            </select>
                          ) : (
                            <input
                              className="input mono"
                              style={{ flex: 1, fontSize: 11.5, height: 26, minWidth: 120 }}
                              type={typeof tv === "number" ? "number" : "text"}
                              value={pin.value === null || pin.value === undefined ? "" : String(pin.value)}
                              placeholder={tv === undefined ? "template" : `template: ${String(tv)}`}
                              title="Run default — applies to every row that doesn't set its own value. Blank = template value."
                              onChange={(e) => commitPinValue(idx, e.target.value)}
                            />
                          )}
                          {isInt && !pin.is_prompt && (
                            <select
                              className="select" style={{ height: 26, fontSize: 11 }}
                              value={pin.int_mode ?? "fixed"}
                              title='When a row has no value: Fixed = default/template; Random = new random integer per row; Increment = default/template + row number'
                              onChange={(e) => patchPin(idx, { int_mode: e.target.value === "fixed" ? null : e.target.value as PinnedParam["int_mode"] })}
                            >
                              {Object.entries(INT_MODE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                            </select>
                          )}
                          <label
                            style={{ fontSize: 11, color: "var(--fg-mute)", display: "flex", alignItems: "center", gap: 4, cursor: "pointer", flexShrink: 0 }}
                            title="The prompt column: bulk paste, .txt import and prompt-as-caption target this parameter. Always per-row."
                          >
                            <input type="radio" name="is-prompt-pin" checked={pin.is_prompt} onChange={() => patchPin(idx, { is_prompt: true })} />
                            ★ prompt
                          </label>
                          <label
                            style={{ fontSize: 11, color: pin.per_row ? "var(--fg)" : "var(--fg-mute)", display: "flex", alignItems: "center", gap: 4, cursor: pin.is_prompt ? "not-allowed" : "pointer", flexShrink: 0 }}
                            title={pin.is_prompt ? "The prompt is always a per-row column" : "On = own column in the queue table; off = run default only"}
                          >
                            <input
                              type="checkbox" className="checkbox" checked={pin.per_row} disabled={pin.is_prompt}
                              onChange={(e) => patchPin(idx, { per_row: e.target.checked })}
                            />
                            per row
                          </label>
                          <button className="icon-btn" title="Unpin" onClick={() => togglePin(pin.node_id, pin.input)}>×</button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Node browser */}
      {workflow && (
        <div className="panel">
          <div className="panel-h">
            <h3>Workflow nodes</h3>
            <div style={{ flex: 1 }} />
            <input
              className="input"
              style={{ width: 240, height: 28, fontSize: 12 }}
              placeholder="Search nodes, inputs, values…"
              value={nodeFilter}
              onChange={(e) => setNodeFilter(e.target.value)}
            />
          </div>
          <div className="panel-b" style={{ maxHeight: "45vh", overflowY: "auto" }}>
            {filteredNodes.length === 0 && (
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
                No nodes match “{nodeFilter}”.
              </p>
            )}
            {filteredNodes.map(([nodeId, node]) => {
              const inputs = scalarInputs(node.inputs ?? {});
              const unpinnedCount = inputs.filter(([input]) => !pinnedSet.has(pinKey(nodeId, input))).length;
              return (
                <div key={nodeId} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                    <span>
                      <span className="mono" style={{ color: "var(--fg-dim)" }}>{nodeId}</span>{" "}
                      {node.class_type}
                      {node._meta?.title && node._meta.title !== node.class_type && (
                        <span style={{ color: "var(--fg-mute)", fontWeight: 400 }}> — {node._meta.title}</span>
                      )}
                    </span>
                    {inputs.length > 1 && unpinnedCount > 0 && (
                      <button
                        className="btn ghost sm" style={{ fontSize: 10.5, padding: "0 8px" }}
                        title={`Pin all ${unpinnedCount} remaining inputs of this node as run defaults`}
                        onClick={() => pinNode(nodeId)}
                      >
                        Pin node ({unpinnedCount})
                      </button>
                    )}
                  </div>
                  {inputs.length === 0 && (
                    <p style={{ fontSize: 11, color: "var(--fg-dim)", margin: 0 }}>
                      No editable inputs (only node connections)
                      {/save|preview/i.test(node.class_type) ? " — selectable under “Import images from” above" : ""}
                    </p>
                  )}
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

      {syncSnapshot && (
        <SyncCanvasModal
          snapshot={syncSnapshot}
          droppedPins={pins.filter((p) => !pinResolves(p, syncSnapshot.workflow))}
          onApply={() => applySync(syncSnapshot)}
          onClose={() => setSyncSnapshot(null)}
        />
      )}

      {showScan && (
        <WorkflowScanModal
          onLoad={(wf, sourceName) => {
            handleTextChange(JSON.stringify(wf));
            setShowScan(false);
            toast.success(`Loaded "${sourceName}" — review and Save to apply`);
          }}
          onClose={() => setShowScan(false)}
        />
      )}
    </div>
  );
}
