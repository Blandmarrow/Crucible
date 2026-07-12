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


def extract_output_images(history_entry: dict) -> list[dict]:
    """Collect {filename, subfolder, type} refs for final outputs (skips previews)."""
    images = []
    for out in (history_entry.get("outputs") or {}).values():
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
    seed_mode: str,
    row_index: int,
) -> dict:
    """Deep-copy the template and apply a row's pinned-parameter values.

    Blank/absent row values keep the template value, except a pin aliased
    "seed" (case-insensitive), where seed_mode applies: fixed = template,
    random = fresh random int, increment = template + row index within the run.
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
        value = row_values.get(alias)
        if value not in (None, ""):
            node["inputs"][input_name] = _coerce(value, template_val)
        elif isinstance(alias, str) and alias.lower() == "seed":
            if seed_mode == "random":
                node["inputs"][input_name] = random.randint(0, 2**48)
            elif seed_mode == "increment" and isinstance(template_val, (int, float)):
                node["inputs"][input_name] = int(template_val) + row_index
            # fixed: keep template value
    return wf


def effective_prompt(plan_workflow: dict, pinned: list[dict], row_values: dict) -> str | None:
    """The prompt text a row will run with: its override, else the template value."""
    for p in pinned:
        if p.get("is_prompt"):
            value = row_values.get(p.get("alias"))
            if value not in (None, ""):
                return str(value)
            node = plan_workflow.get(str(p.get("node_id"))) or {}
            tv = (node.get("inputs") or {}).get(p.get("input"))
            return str(tv) if tv not in (None, "") else None
    return None
