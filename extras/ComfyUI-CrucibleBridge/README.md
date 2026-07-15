# ComfyUI-CrucibleBridge

A tiny ComfyUI extension that publishes the **currently open canvas** so
Crucible's **Sync from canvas** button (ComfyUI page → Workflow & Pins) can pull
your latest workflow edits without you queueing a prompt or re-running
*Workflow → Export (API)*.

How it works: a small JS extension runs in your ComfyUI browser tab. Whenever
the graph changes (debounced), it converts the canvas with `app.graphToPrompt()`
— the exact conversion Queue Prompt uses, so bypassed/muted nodes are resolved —
and POSTs the result to a route this package registers on the ComfyUI server
(`/crucible/active_workflow`). Crucible's backend reads that snapshot. Nothing
is queued, executed, or written to ComfyUI's history.

## Install

1. Copy (or symlink) this folder into your ComfyUI install:

   ```
   ComfyUI/custom_nodes/ComfyUI-CrucibleBridge/
   ```

2. Restart ComfyUI and reload the browser tab.

There are no nodes to add and no configuration. To verify it's active, open
`http://<comfyui-host>:<port>/crucible/active_workflow` — you should see JSON
(`{"workflow": null}` until a tab has pushed).

## Caveats

- **A ComfyUI browser tab must be open** — the canvas lives in the browser, so
  the snapshot only updates while a tab is running the extension.
- **Multiple tabs**: last writer wins — the snapshot is whichever tab changed
  most recently.
- **Workflow tabs**: within one browser tab, the snapshot tracks the focused
  workflow tab (whatever you're looking at).
- Restarting ComfyUI clears the snapshot until a tab pushes again.

## Without the bridge

Crucible falls back to the **last-queued prompt** (`GET /history`): edit in
ComfyUI, press *Queue Prompt* once, then sync in Crucible. The bridge exists to
remove that queue-once friction.
