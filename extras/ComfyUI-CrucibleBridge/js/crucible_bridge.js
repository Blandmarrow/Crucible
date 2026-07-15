// CrucibleBridge: push the open canvas (as an API-format prompt) to the bridge
// route whenever the graph changes, so Crucible can "Sync from canvas" without
// the user queueing or re-exporting. Same-origin fetch — no CORS involved.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const DEBOUNCE_MS = 750;
const POLL_MS = 5000;

let lastPushed = null;
let timer = null;

// The active-workflow accessor has moved between ComfyUI frontend versions;
// try the known homes and fall back to null (name is cosmetic metadata only).
function workflowName() {
  return (
    app.extensionManager?.workflow?.activeWorkflow?.filename ??
    app.workflowManager?.activeWorkflow?.name ??
    null
  );
}

async function push() {
  let output;
  try {
    // graphToPrompt is the exact conversion Queue Prompt uses (handles
    // bypass/mute rewiring); it can throw mid-edit on an invalid graph —
    // skip silently, a later tick will retry.
    ({ output } = await app.graphToPrompt());
  } catch {
    return;
  }
  if (!output || Object.keys(output).length === 0) return;
  const serialized = JSON.stringify(output);
  if (serialized === lastPushed) return; // steady-state polls are no-ops
  try {
    await api.fetchApi("/crucible/active_workflow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workflow: output,
        name: workflowName(),
        node_count: Object.keys(output).length,
        client_time: Date.now(),
      }),
    });
    lastPushed = serialized;
  } catch {
    // Server hiccup — leave lastPushed unset so the next tick retries.
  }
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(push, DEBOUNCE_MS);
}

app.registerExtension({
  name: "Crucible.Bridge",
  async setup() {
    // "graphChanged" drives ComfyUI's own autosave but isn't guaranteed across
    // frontend versions — the interval below is the safety net either way.
    api.addEventListener?.("graphChanged", schedule);
    setInterval(schedule, POLL_MS);
    schedule(); // publish once on tab load, before any edit
  },
});
