# ComfyUI workflow sync

Pull a plan's `workflow_json` from ComfyUI instead of re-pasting *Export (API)* after
every canvas edit. Two transports behind one endpoint: a live-canvas snapshot pushed by
the `ComfyUI-CrucibleBridge` extension (main path), and the last-queued history entry
(fallback, requires one real Queue Prompt). See `docs/dev/comfyui.md` for the plan/pin
data model.

## Background / constraints (verified against a live ComfyUI 0.24 instance)

- ComfyUI's server has **no "current canvas" endpoint** — editor state lives in the browser
  until the user queues or saves. Crucible also cannot trigger a queue of the open canvas
  (queueing *is* the browser converting the canvas and POSTing it).
- `GET /history?max_items=1` returns the last **queued** prompt; `entry["prompt"][2]` is the
  full API-format workflow with bypassed/muted nodes already resolved — identical to what
  *Export (API)* produces. (`prompt` is `[number, prompt_id, workflow, extra_data, outputs]`.)
- `GET /userdata?dir=workflows` lists saved workflows but only in **UI format**. UI→API
  conversion (incl. bypass `mode: 4` rewiring) lives in ComfyUI's frontend; we deliberately
  do not reimplement it server-side.
- In the browser, `app.graphToPrompt()` is the exact function Queue Prompt uses; custom node
  packages can ship frontend JS plus server routes via `PromptServer.instance.routes`.

## Backend

`ComfyClient` (`backend/services/comfy_service.py`) has two sync methods (10 s timeout —
small metadata GETs): `bridge_snapshot()` — `GET /crucible/active_workflow`, **404 → None**
(bridge not installed), other errors raise; `last_history_entry()` — `GET /history?max_items=1`,
newest entry or None. Pure helper `history_entry_workflow(entry)` extracts `entry["prompt"][2]`
with shape guards and returns it only when `workflow_format(...) == "api"`.

`GET /comfy/canvas-workflow` (`backend/routers/comfy.py`, next to `/ping`): reads
`comfyui_url` (400 if unset), tries the bridge snapshot first, falls back to history when the
bridge is missing (404), empty (`workflow: null`), or holds a non-API payload. `httpx.HTTPError`
→ 502. Neither source → 404 with install-the-bridge / queue-once guidance. Response:

```json
{"workflow": {...}, "source": "bridge"|"history", "name": "Anima_v9"|null,
 "node_count": 33, "age_seconds": 4.2|null}
```

`name`/`age_seconds` are bridge-only (history has neither; both null).

## `extras/ComfyUI-CrucibleBridge/` (the bridge extension)

Distributed from this repo; the user copies/symlinks it into ComfyUI's `custom_nodes/` and
restarts (install docs in its README.md). No nodes — routes + frontend JS only:

- **`__init__.py`**: one in-memory snapshot slot (`{workflow, name, node_count}` +
  `time.monotonic()` receipt stamp — monotonic so `age_seconds` is immune to clock skew
  between browser/ComfyUI/Crucible hosts). `POST /crucible/active_workflow` stores (400 on
  non-dict workflow); `GET` returns the snapshot with computed `age_seconds`, or
  `{"workflow": null}` (200) when nothing pushed yet — distinct from the route-404 that means
  "not installed". Exports `NODE_CLASS_MAPPINGS = {}`, `WEB_DIRECTORY = "./js"`.
- **`extras/ComfyUI-CrucibleBridge/js/crucible_bridge.js`**: `app.registerExtension` setup wires a 750 ms-debounced
  `push()` to `api.addEventListener?.("graphChanged", …)` **plus** a 5 s `setInterval`
  safety net (the event name isn't guaranteed across frontend versions) plus one initial
  call on tab load. `push()` = `app.graphToPrompt()` → `.output` → skip if stringified
  output unchanged since last push (steady-state polls are no-ops) → `api.fetchApi` POST
  (same origin, no CORS). graphToPrompt can throw mid-edit — swallowed, next tick retries.
  Workflow name via a defensive accessor chain (`app.extensionManager?.workflow?.…` →
  `app.workflowManager?.…` → null); it has moved between frontend versions and is cosmetic.

Semantics: requires an open ComfyUI browser tab; multiple tabs last-writer-wins (single
slot, deliberate); within a tab the snapshot tracks the focused workflow tab; a ComfyUI
restart clears the snapshot until a tab pushes again.

## Frontend ("Sync from canvas")

Button in `WorkflowPinPanel`'s header (next to *Scan folder…*) → `comfyApi.canvasWorkflow()`
→ `SyncCanvasModal` (`frontend/src/components/comfy/SyncCanvasModal.tsx`) shows source badge
(live canvas / last queued), name + node count + age, a stale-snapshot warning past 5 min,
a bridge-install hint on the history path, and the list of pins that will be dropped.

Pin keep/drop is client-side, mirroring `patch_workflow`'s resolution check:
`pin.node_id in wf && pin.input in (wf[pin.node_id].inputs ?? {})`. Non-resolving pins
(node removed or bypassed away) are listed in the dialog and removed on apply — otherwise
they'd fail at run time. `output_node_ids` are deliberately untouched; the panel's existing
`missingOutputNodes` warning covers stale ones (no-auto-heal stance).

**Apply commits immediately** (user decision): the dialog's *Replace workflow* runs the
alias guards and calls `onSave({workflow_json, pinned_params: keptPins, output_node_ids})`
— one `PATCH /comfy/plans/{id}` through `ComfyPage`'s existing `updatePlanMutation`
(invalidates `["comfy","plan",id]` + `["comfy","plans",datasetId]`; the panel remounts via
`key={plan.id + plan.updated_at}`). Note this also commits any unsaved pin edits sitting in
the panel — it saves the whole panel state, same as the Save button. This differs from the
Scan-folder flow, which only stages into the textarea.
