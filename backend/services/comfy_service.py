"""ComfyUI HTTP API client + workflow patching for the generation queue.

Pure service layer: no DB access, no HTTP routing. The job worker in
routers/comfy.py drives ComfyClient per row; patch_workflow applies a row's
pinned-parameter values onto a deep copy of the plan's API-format workflow.
"""
import asyncio
import copy
import logging
import random
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Generation latency is handled by the poll loop, not the read timeout.
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

# Upper bound on a single generation before the row is failed.
PER_ROW_TIMEOUT_S = 1800.0

# Consecutive connection failures before the whole run is aborted (server down).
MAX_CONSECUTIVE_CONNECT_ERRORS = 3

# How long cancel_prompt keeps re-reading /queue to confirm our prompt is gone,
# and how often. Module constants, read at call time, so tests can shrink them.
CANCEL_CONFIRM_WINDOW_S = 5.0
CANCEL_CONFIRM_INTERVAL_S = 0.5

# Per-request timeout for the queue-cleanup calls (GET/POST /queue, /interrupt).
_CLEANUP_TIMEOUT = 5.0


class ComfyRowError(Exception):
    """Per-row failure (bad workflow patch, ComfyUI rejection, no outputs, timeout)."""


@dataclass(frozen=True)
class QueueItem:
    """One entry of ComfyUI's GET /queue: the prompt id and the submitting client_id."""

    prompt_id: str
    client_id: str | None


@dataclass(frozen=True)
class QueueState:
    running: list[QueueItem]
    pending: list[QueueItem]


def _parse_queue_items(items) -> list[QueueItem]:
    """Queue items are [number, prompt_id, prompt, extra_data, outputs]; extra_data
    carries the submitting client_id. Malformed entries are skipped."""
    out: list[QueueItem] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) < 2 or not item[1]:
            continue
        extra = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
        cid = extra.get("client_id")
        out.append(QueueItem(prompt_id=str(item[1]), client_id=str(cid) if cid else None))
    return out


class ComfyClient:
    """Thin async client for ComfyUI's native HTTP API.

    `transport` is handed to every httpx.AsyncClient the methods open (tests pass
    an httpx.MockTransport); None means httpx's default network transport.
    """

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    async def ping(self) -> dict:
        """GET /system_stats — raises httpx errors on failure."""
        async with httpx.AsyncClient(timeout=5.0, transport=self._transport) as client:
            resp = await client.get(f"{self.base_url}/system_stats")
            resp.raise_for_status()
            return resp.json()

    async def submit(self, workflow: dict, client_id: str) -> str:
        """POST /prompt — returns the ComfyUI prompt_id, or raises ComfyRowError."""
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
            resp = await client.get(f"{self.base_url}/history/{prompt_id}")
            resp.raise_for_status()
            data = resp.json()
        return data.get(prompt_id)

    async def bridge_snapshot(self) -> dict | None:
        """GET /crucible/active_workflow (ComfyUI-CrucibleBridge extension).

        Returns the parsed snapshot body, or None when the bridge is not
        installed (route 404). Other HTTP errors raise.
        """
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            resp = await client.get(f"{self.base_url}/crucible/active_workflow")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def last_history_entry(self) -> dict | None:
        """GET /history?max_items=1 — the newest queued entry, or None if empty."""
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            resp = await client.get(f"{self.base_url}/history", params={"max_items": 1})
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict) or not data:
            return None
        return next(iter(data.values()))

    async def fetch_image(self, filename: str, subfolder: str = "", type: str = "output") -> bytes:
        """GET /view — raw bytes of a generated image."""
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
            resp = await client.get(
                f"{self.base_url}/view",
                params={"filename": filename, "subfolder": subfolder, "type": type},
            )
            resp.raise_for_status()
            return resp.content

    async def _get_queue(self, client: httpx.AsyncClient) -> QueueState:
        resp = await client.get(f"{self.base_url}/queue")
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"unexpected /queue body: {data!r}"[:200])
        return QueueState(
            running=_parse_queue_items(data.get("queue_running")),
            pending=_parse_queue_items(data.get("queue_pending")),
        )

    async def queue_state(self) -> QueueState:
        """GET /queue — the running and pending prompts. Raises httpx errors."""
        async with httpx.AsyncClient(timeout=_CLEANUP_TIMEOUT, transport=self._transport) as client:
            return await self._get_queue(client)

    async def cancel_prompt(self, prompt_id: str | None, client_id: str | None) -> str:
        """Take *our* prompt out of ComfyUI — never anybody else's.

        "Ours" is `prompt_id` plus every queue item whose extra_data client_id is
        `client_id` (the job id we submit with), which also covers a cancel that
        landed during submit, before we learned the prompt id:

        1. GET /queue, then POST /queue {"delete": [...]} for `prompt_id` (always —
           deleting an id that is not pending is a no-op) plus every pending item
           of ours. Pending-only; never {"clear": true}.
        2. Re-read /queue; for each of ours in queue_running, POST /interrupt
           {"prompt_id": id}. Never a body-less interrupt: that is global and
           kills whatever is executing, e.g. the user's own canvas prompt.
        3. Keep re-reading /queue every CANCEL_CONFIRM_INTERVAL_S, for at most
           CANCEL_CONFIRM_WINDOW_S, until none of ours is left (re-deleting, or
           interrupting, anything of ours that moved in the meantime).

        Returns, never raising for any of them:
        "not_found"     — nothing of ours was queued or running;
        "removed"       — ours was pending and has been deleted;
        "interrupted"   — ours was running and stopped after a targeted interrupt;
        "still_running" — ours could not be confirmed gone within the window
                          (including a /queue that errors or returns garbage);
        "unreachable"   — an httpx transport error (connect refused, timeout, ...).

        Residual risk: a ComfyUI old enough to ignore the /interrupt body treats
        it as a global interrupt. If our prompt finishes in the milliseconds
        between the GET /queue that saw it running and the interrupt POST, that
        interrupt lands on whatever ComfyUI runs next.
        """
        if not prompt_id and not client_id:
            return "not_found"

        def ours(item: QueueItem) -> bool:
            return bool(
                (prompt_id and item.prompt_id == prompt_id)
                or (client_id and item.client_id == client_id)
            )

        loop = asyncio.get_running_loop()
        try:
            async with httpx.AsyncClient(timeout=_CLEANUP_TIMEOUT, transport=self._transport) as client:
                state = await self._get_queue(client)
                pending = [it.prompt_id for it in state.pending if ours(it)]
                to_delete = list(dict.fromkeys(([prompt_id] if prompt_id else []) + pending))
                if to_delete:
                    await client.post(f"{self.base_url}/queue", json={"delete": to_delete})
                removed = bool(pending)
                interrupted: set[str] = set()
                deadline = loop.time() + CANCEL_CONFIRM_WINDOW_S
                while True:
                    state = await self._get_queue(client)
                    running = [it.prompt_id for it in state.running if ours(it)]
                    pending = [it.prompt_id for it in state.pending if ours(it)]
                    if not running and not pending:
                        if interrupted:
                            return "interrupted"
                        return "removed" if removed else "not_found"
                    if loop.time() >= deadline:
                        logger.warning(
                            "ComfyUI cleanup: still queued after %.1fs (running=%s pending=%s)",
                            CANCEL_CONFIRM_WINDOW_S, running, pending,
                        )
                        return "still_running"
                    if pending:
                        await client.post(f"{self.base_url}/queue", json={"delete": pending})
                        removed = True
                    for pid in running:
                        if pid not in interrupted:
                            await client.post(f"{self.base_url}/interrupt", json={"prompt_id": pid})
                            interrupted.add(pid)
                    await asyncio.sleep(CANCEL_CONFIRM_INTERVAL_S)
        except httpx.TransportError:
            logger.warning("ComfyUI cleanup: unreachable at %s", self.base_url, exc_info=True)
            return "unreachable"
        except (httpx.HTTPStatusError, ValueError):
            # /queue answered, but not with anything we can read: our prompt cannot
            # be confirmed gone, so report the conservative outcome.
            logger.warning("ComfyUI cleanup: could not read /queue at %s", self.base_url, exc_info=True)
            return "still_running"


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


def _lenient_int(value) -> int:
    """int() with a float fallback so numeric strings the frontend intentionally
    keeps as strings ('1e5', '1.0', '.5' — see coerceCellValue in api/comfy.ts)
    still coerce; garbage still raises ValueError/TypeError."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return int(float(value))


def _coerce(value, template_val):
    """Coerce a row value to the template value's JSON type. The authority for
    typing: the frontend deliberately sends numeric strings when converting to
    a JS number would lose precision (e.g. seeds beyond 2^53)."""
    try:
        if isinstance(template_val, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(template_val, int):
            return _lenient_int(value)
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


def history_entry_workflow(entry: dict) -> dict | None:
    """The API-format workflow embedded in a history entry, or None.

    entry["prompt"] is [number, prompt_id, workflow, extra_data, outputs];
    index 2 is the workflow exactly as Export (API) would produce it (bypassed/
    muted nodes already resolved). Returns None when the shape is unexpected or
    the payload is not API-format per workflow_format.
    """
    prompt = entry.get("prompt")
    if not isinstance(prompt, (list, tuple)) or len(prompt) < 3:
        return None
    wf = prompt[2]
    return wf if workflow_format(wf) == "api" else None


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
        if int_mode == "increment":
            # base_val may be a numeric string (big seeds are kept as strings by
            # the frontend to avoid 2^53 precision loss) — coerce before adding.
            try:
                node["inputs"][input_name] = _lenient_int(base_val) + row_index
                continue
            except (ValueError, TypeError):
                pass  # non-numeric base: fall through to default/template as before
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
