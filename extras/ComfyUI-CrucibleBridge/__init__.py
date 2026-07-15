"""ComfyUI-CrucibleBridge — publish the open ComfyUI canvas to Crucible.

The bundled JS extension (js/crucible_bridge.js) runs in every ComfyUI browser
tab and POSTs the canvas as an API-format prompt (via app.graphToPrompt(), the
exact conversion Queue Prompt uses) whenever the graph changes. This module
holds the latest snapshot in memory and serves it to Crucible's
"Sync from canvas" feature. No nodes, no execution, no history entries.

Snapshot semantics: one in-memory slot — multiple tabs are last-writer-wins,
and within a tab the snapshot tracks the focused workflow tab. Restarting
ComfyUI clears it until a tab pushes again.
"""
import time

from aiohttp import web

from server import PromptServer

_snapshot: dict | None = None  # {"workflow", "name", "node_count"}
_received: float | None = None  # time.monotonic() at POST — immune to clock skew

routes = PromptServer.instance.routes


@routes.post("/crucible/active_workflow")
async def post_active_workflow(request: web.Request) -> web.Response:
    global _snapshot, _received
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    workflow = body.get("workflow")
    if not isinstance(workflow, dict) or not workflow:
        return web.json_response({"error": "workflow must be a non-empty object"}, status=400)
    _snapshot = {
        "workflow": workflow,
        "name": body.get("name"),
        "node_count": body.get("node_count") or len(workflow),
    }
    _received = time.monotonic()
    return web.json_response({"ok": True})


@routes.get("/crucible/active_workflow")
async def get_active_workflow(request: web.Request) -> web.Response:
    # workflow: null (200) = bridge installed but nothing pushed yet — distinct
    # from a 404 route miss, which Crucible reads as "bridge not installed".
    if _snapshot is None or _received is None:
        return web.json_response({"workflow": None})
    return web.json_response({**_snapshot, "age_seconds": time.monotonic() - _received})


NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
