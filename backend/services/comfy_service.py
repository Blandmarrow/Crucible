"""ComfyUI HTTP API client + workflow patching for the generation queue.

Pure service layer: no DB access, no HTTP routing. The job worker in
routers/comfy.py drives ComfyClient per row; patch_workflow applies a row's
pinned-parameter values onto a deep copy of the plan's API-format workflow.
"""
import copy
import logging
import random

import httpx

logger = logging.getLogger(__name__)

# Generation latency is handled by the poll loop, not the read timeout.
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

# Upper bound on a single generation before the row is failed.
PER_ROW_TIMEOUT_S = 1800.0

# Consecutive connection failures before the whole run is aborted (server down).
MAX_CONSECUTIVE_CONNECT_ERRORS = 3


class ComfyRowError(Exception):
    """Per-row failure (bad workflow patch, ComfyUI rejection, no outputs, timeout)."""


class ComfyClient:
    """Thin async client for ComfyUI's native HTTP API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def ping(self) -> dict:
        """GET /system_stats — raises httpx errors on failure."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.base_url}/system_stats")
            resp.raise_for_status()
            return resp.json()

    async def submit(self, workflow: dict, client_id: str) -> str:
        """POST /prompt — returns the ComfyUI prompt_id, or raises ComfyRowError."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
            )
        if resp.status_code == 400:
            raise ComfyRowError(f"ComfyUI rejected workflow: {_format_node_errors(resp)}")
        resp.raise_for_status()
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyRowError(f"ComfyUI /prompt returned no prompt_id: {data}")
        return prompt_id

    async def poll_history(self, prompt_id: str) -> dict | None:
        """GET /history/{prompt_id} — the entry dict once execution finished, else None."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{self.base_url}/history/{prompt_id}")
            resp.raise_for_status()
            data = resp.json()
        return data.get(prompt_id)

    async def fetch_image(self, filename: str, subfolder: str = "", type: str = "output") -> bytes:
        """GET /view — raw bytes of a generated image."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{self.base_url}/view",
                params={"filename": filename, "subfolder": subfolder, "type": type},
            )
            resp.raise_for_status()
            return resp.content

    async def interrupt(self) -> None:
        """POST /interrupt — best effort, only affects the currently executing prompt."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(f"{self.base_url}/interrupt")
        except Exception:
            pass


def _format_node_errors(resp: httpx.Response) -> str:
    """Flatten a ComfyUI 400 body ({error, node_errors}) into a readable message."""
    try:
        body = resp.json()
    except Exception:
        return resp.text[:500]
    parts = []
    err = body.get("error")
    if isinstance(err, dict):
        parts.append(err.get("message") or str(err))
    elif err:
        parts.append(str(err))
    for node_id, ne in (body.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            msg = e.get("message", "")
            detail = e.get("details", "")
            parts.append(f"node {node_id}: {msg}{f' ({detail})' if detail else ''}")
    return "; ".join(p for p in parts if p) or resp.text[:500]


def _coerce(value, template_val):
    """Coerce a row value to the template value's JSON type (safety net —
    the frontend already sends native types based on the template)."""
    try:
        if isinstance(template_val, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(template_val, int):
            return int(value)
        if isinstance(template_val, float):
            return float(value)
    except (ValueError, TypeError) as e:
        raise ComfyRowError(f"cannot convert {value!r} to {type(template_val).__name__}: {e}")
    return value


def workflow_format(obj) -> str:
    """Classify a parsed workflow JSON: "api", "ui", or "invalid".

    Mirrors the client-side isApiFormat check in WorkflowPinPanel: an API-format
    export is a non-empty {node_id: {class_type, inputs}} mapping; a UI-format
    editor export has "nodes"/"links" keys and cannot be executed directly.
    """
    if not isinstance(obj, dict) or not obj:
        return "invalid"
    if "nodes" in obj or "links" in obj:
        return "ui"
    if all(
        isinstance(n, dict) and "class_type" in n and "inputs" in n
        for n in obj.values()
    ):
        return "api"
    return "invalid"


def extract_output_images(history_entry: dict, output_node_ids: list | None = None) -> list[dict]:
    """Collect {filename, subfolder, type} refs to import from a history entry.

    Default (no output_node_ids): all type=="output" images — what SaveImage
    nodes produce; temp previews are skipped. With output_node_ids: those
    nodes' images regardless of type (in the given node order), so PreviewImage
    nodes can be import sources for workflows that have no SaveImage node
    (their temp files are downloaded immediately, before ComfyUI clears them).
    """
    outputs = history_entry.get("outputs") or {}
    images: list[dict] = []
    if output_node_ids:
        for node_id in output_node_ids:
            node_out = outputs.get(str(node_id)) or {}
            images.extend(img for img in node_out.get("images", []) if img.get("filename"))
        return images
    for out in outputs.values():
        for img in out.get("images", []):
            if img.get("type") == "output" and img.get("filename"):
                images.append(img)
    return images


def check_history_error(history_entry: dict) -> str | None:
    """Return a readable error message if the history entry reports failure.

    Older ComfyUI versions omit `status` entirely — presence of the entry then
    means completion, so only check status_str when present.
    """
    status = history_entry.get("status") or {}
    if status.get("status_str") == "error":
        msgs = []
        for m in status.get("messages", []):
            # messages are [event_name, payload] pairs
            if isinstance(m, (list, tuple)) and len(m) > 1 and isinstance(m[1], dict):
                em = m[1].get("exception_message")
                if em:
                    msgs.append(str(em))
        return "; ".join(msgs) or "ComfyUI reported an execution error"
    return None


def patch_workflow(
    workflow: dict,
    pinned: list[dict],
    row_values: dict,
    row_index: int,
) -> dict:
    """Deep-copy the template and apply pinned-parameter values for one row.

    Value resolution per pin, first hit wins:
      1. the row's value (only meaningful for per_row pins — non-column pins
         have no row values by construction),
      2. the pin's `int_mode` (integer params only): random = fresh
         random int, increment = base + row index within the run (base = run
         default or template),
      3. the pin's run-default `value`,
      4. the template value (pin untouched).
    """
    wf = copy.deepcopy(workflow)
    for p in pinned:
        node_id, input_name, alias = p.get("node_id"), p.get("input"), p.get("alias")
        node = wf.get(str(node_id))
        if node is None or "inputs" not in node:
            raise ComfyRowError(f"pinned node {node_id!r} not found in workflow")
        if input_name not in node["inputs"]:
            raise ComfyRowError(f"input {input_name!r} not found on node {node_id!r}")
        template_val = node["inputs"][input_name]
        default_val = p.get("value")
        base_val = default_val if default_val not in (None, "") else template_val

        row_val = row_values.get(alias)
        if row_val not in (None, ""):
            node["inputs"][input_name] = _coerce(row_val, template_val)
            continue
        int_mode = p.get("int_mode")
        if int_mode == "random":
            node["inputs"][input_name] = random.randint(0, 2**48)
            continue
        if int_mode == "increment" and isinstance(base_val, (int, float)):
            node["inputs"][input_name] = int(base_val) + row_index
            continue
        if default_val not in (None, ""):
            node["inputs"][input_name] = _coerce(default_val, template_val)
    return wf


def effective_prompt(plan_workflow: dict, pinned: list[dict], row_values: dict) -> str | None:
    """The prompt text a row runs with: row override, else the prompt pin's
    run default, else the template value."""
    for p in pinned:
        if p.get("is_prompt"):
            value = row_values.get(p.get("alias"))
            if value not in (None, ""):
                return str(value)
            default_val = p.get("value")
            if default_val not in (None, ""):
                return str(default_val)
            node = plan_workflow.get(str(p.get("node_id"))) or {}
            tv = (node.get("inputs") or {}).get(p.get("input"))
            return str(tv) if tv not in (None, "") else None
    return None
