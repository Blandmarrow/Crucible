# Roadmap: ComfyUI workflow sync (planned, not yet implemented)

Goal: stop treating a plan's `workflow_json` as a one-time paste. Let Crucible pull the
workflow state from ComfyUI so edits made there (e.g. bypassing a node) carry over without
re-exporting. Design agreed 2026-07-12; build after core `comfy_generate` functionality is
stable. See `docs/dev/comfyui.md` for the existing data model.

## Background / constraints (verified against a live ComfyUI 0.24 instance)

- ComfyUI's server has **no "current canvas" endpoint** — editor state lives in the browser
  until the user queues or saves. Crucible also cannot trigger a queue of the open canvas
  (queueing *is* the browser converting the canvas and POSTing it), so "queue then cancel"
  approaches are circular.
- `GET /history?max_items=1` returns the last **queued** prompt; `entry["prompt"][2]` is the
  full API-format workflow with bypassed/muted nodes already resolved — identical to what
  *Export (API)* produces.
- `GET /userdata?dir=workflows` lists saved workflows but only in **UI format** (`nodes`/
  `links`). UI→API conversion (incl. bypass `mode: 4` rewiring) lives in ComfyUI's frontend;
  do not reimplement it server-side.
- In the browser, `app.graphToPrompt()` is the exact function Queue Prompt uses; custom node
  packages can ship frontend JS plus server routes via `PromptServer.instance.routes`.

## 1. History-pull fallback (build first — nearly free)

"Pull last-queued workflow" in `WorkflowPinPanel`: backend endpoint proxies
`GET {comfyui_url}/history?max_items=1`, extracts `prompt[2]`, replaces the plan's
`workflow_json`. User flow: edit in ComfyUI → Queue Prompt once → pull in Crucible.
Friction (requires one real queue) is why this is the fallback, not the main path.

## 2. `ComfyUI-CrucibleBridge` custom node package (the main path)

Small package dropped into ComfyUI's `custom_nodes/` (~50 lines Python + ~40 lines JS):

- **JS extension** (runs in the ComfyUI tab): on graph change (debounced), call
  `app.graphToPrompt()` and POST the API-format output — plus workflow name, node count,
  timestamp — to the bridge route. No queueing, no execution, no history entry.
- **Python side**: registers `GET /crucible/active_workflow` returning the latest snapshot
  (held in memory).

Caveats: requires a ComfyUI browser tab to be open; multiple browser tabs last-writer-wins;
within one tab the snapshot tracks the focused workflow tab (i.e. whatever the user is
looking at).

## 3. "Sync from canvas" button in Crucible

In `WorkflowPinPanel`: try the bridge route first, fall back to history-pull if the bridge
isn't installed (404). Before applying, show a confirmation with the snapshot's workflow
name, node count, and age ("Canvas: Anima_v9 — 33 nodes, pushed 4 s ago — replace this
plan's workflow?") so multi-tab ambiguity is visible. On apply: keep pins whose
`{node_id, input}` still resolve in the new workflow; warn and drop the ones that don't
(e.g. the pinned node was bypassed away — otherwise a silent run-time failure).
