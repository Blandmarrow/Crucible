"""A fake ComfyUI, shared by pytest and the Playwright e2e suite.

One handler, two adapters. `FakeComfy.handler(httpx.Request) -> httpx.Response`
holds all the behaviour. pytest wraps it in `httpx.MockTransport` (`transport()`)
and hands that to `ComfyClient`; the e2e suite runs the same handler on a real
port (`serve()`, or `python -m backend.tests.fake_comfy --port 8198`), because
its backend is a separate process the transport seam cannot reach.

**Self-contained on purpose.** This module imports only the stdlib, httpx and
Pillow — never `conftest`, which imports `backend.config`/`backend.database` and
the app. The e2e server process must not open a database or read app settings
just to pretend to be ComfyUI. It is not named `test_*`, so pytest does not
collect it.

What it models:

- A queue with a running and a pending list. `POST /queue {"delete"}` touches
  pending prompts only. `POST /interrupt` is body-aware and stops only the named
  prompt; a body-less (global) interrupt is recorded as a violation and stops
  whatever runs, as an old server would.
- `/prompt` validates as far as Crucible can get wrong: a graph with no output
  node is a 400 `prompt_no_outputs`, and a link (`[node_id, slot]`) to a node the
  graph lacks is a 400 with `node_errors`.
- On `complete=True`, a submit produces one image per `SaveImage` (type
  `output`) and `PreviewImage` (type `temp`) node of the *submitted* graph. A
  `SaveImage`'s `filename_prefix` splits into subfolder and file prefix, as in
  ComfyUI. The history entry has the real shape — `prompt`, `outputs`, `status`.
  `/view` serves a PNG with the chunks real `SaveImage` writes: `prompt` (the
  submitted graph) always, plus one per key of `extra_data.extra_pnginfo`.
  `ComfyClient.submit` sends no `extra_data`, so a run's images carry `prompt`
  only, and no `workflow` chunk.
- `polls_before_done` keeps a completed prompt `running`, and its history
  empty, for that many polls, so a UI can see a row in flight.
- Failure scenarios picked by a marker anywhere in the submitted graph, so a
  browser spec can choose one per row through the prompt text alone:
  `[[fake:reject]]` → 400 with `node_errors`; `[[fake:fail]]` → a history entry
  with `status_str: "error"`; `[[fake:corrupt]]` → the last output image in
  *graph* order is served as junk bytes, so with outputs listed in that order the
  import fails after earlier outputs were written; `[[fake:partial]]` → a 200
  whose `node_errors` names the last output node in graph order, which is dropped
  while the rest is queued, as ComfyUI does when only some outputs validate.
- `POST /free` (recorded in `frees`) and `GET /history?max_items=N`.

What it does not model: websockets, progress previews, `/upload/image`,
`/object_info`, the ComfyUI-CrucibleBridge snapshot route, input types and
values (only links are checked), and an interrupted prompt's history entry.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

CANVAS_CLIENT = "canvas-browser-tab"
CANVAS_PROMPT = "CANVAS-1"

REJECT_MARKER = "[[fake:reject]]"
FAIL_MARKER = "[[fake:fail]]"
CORRUPT_MARKER = "[[fake:corrupt]]"
PARTIAL_MARKER = "[[fake:partial]]"

FAIL_MESSAGE = "Allocation on device failed (fake out-of-memory)"
REJECT_MESSAGE = "Value not in list"
PARTIAL_MESSAGE = "Required input is missing"

# The ComfyUI node types that produce images, and the history `type` each writes.
_OUTPUT_TYPES = {"SaveImage": "output", "PreviewImage": "temp"}


def _is_link(value) -> bool:
    """An API-format input that names another node's output: `[node_id, slot]`."""
    return (
        isinstance(value, list) and len(value) == 2
        and isinstance(value[0], str) and isinstance(value[1], int)
    )


def _validation_error(graph: dict) -> dict | None:
    """The 400 body ComfyUI would answer for `graph`, or None if it would queue it."""
    if not any(isinstance(n, dict) and n.get("class_type") in _OUTPUT_TYPES for n in graph.values()):
        return {
            "error": {"type": "prompt_no_outputs", "message": "Prompt has no outputs",
                      "details": "", "extra_info": {}},
            "node_errors": {},
        }
    node_errors: dict[str, dict] = {}
    for node_id, node in graph.items():
        for name, value in ((node or {}).get("inputs") or {}).items():
            if _is_link(value) and value[0] not in graph:
                node_errors.setdefault(str(node_id), {"class_type": node.get("class_type"), "errors": []})
                node_errors[str(node_id)]["errors"].append(
                    {"message": "Linked node not found", "details": f"{name}: node {value[0]} is not in the graph"})
    if node_errors:
        return {
            "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
            "node_errors": node_errors,
        }
    return None


def make_png(color=(10, 120, 200), size=(32, 32), **text) -> bytes:
    """A real PNG carrying `text` as tEXt chunks. Own copy of conftest's
    `png_bytes`, because this module must not import conftest."""
    from PIL import Image as PilImage
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for k, v in text.items():
        info.add_text(k, v)
    buf = io.BytesIO()
    PilImage.new("RGB", size, color).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


class FakeComfy:
    """Minimal ComfyUI. Queue entries are [prompt_id, client_id] pairs."""

    def __init__(
        self,
        *,
        canvas_running: bool = True,
        ours_start_running: bool = False,
        honour_cleanup: bool = True,
        unreachable: bool = False,
        queue_status: int = 200,
        complete: bool = False,
        down_after_submits: int | None = None,
        polls_before_done: int = 0,
    ):
        self.complete = complete
        # Goes unreachable for every request once this many prompts were accepted.
        self.down_after_submits = down_after_submits
        self.polls_before_done = polls_before_done
        self.running: list[list] = [[CANVAS_PROMPT, CANVAS_CLIENT]] if canvas_running else []
        self.pending: list[list] = []
        self.history: dict[str, dict] = {}
        self.ours_start_running = ours_start_running
        self.honour_cleanup = honour_cleanup
        self.unreachable = unreachable
        self.queue_status = queue_status
        self.submitted: list[str] = []
        # How many prompts with the submitting client_id were still in ComfyUI's
        # queue at each submit — anything above 0 is a row stacked on a leftover.
        self.ours_outstanding_at_submit: list[int] = []
        self.delete_requests: list[list] = []
        self.deleted: list[str] = []
        self.interrupts: list[str] = []
        self.violations: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.frees: list[dict] = []
        # Completed prompts still "running": prompt_id -> (polls left, finished entry).
        self._finishing: dict[str, list] = {}
        # What /view serves: (filename, subfolder, type) -> bytes.
        self.images: dict[tuple[str, str, str], bytes] = {}
        self._image_counter = 0

    def queue_ids(self, client_id: str | None = None) -> list[str]:
        return [pid for pid, cid in self.running + self.pending if client_id is None or cid == client_id]

    # ── Execution ────────────────────────────────────────────────────────────

    def _execute(
        self, pid: str, number: int, graph: dict, extra_data: dict, skip_outputs: set[str] = frozenset(),
    ) -> dict:
        """The history entry ComfyUI would record for `graph`, with its images
        stored. `skip_outputs` are output nodes that failed validation: ComfyUI
        keeps the whole graph in the prompt but never runs them."""
        text = json.dumps(graph)
        prompt = [number, pid, graph, extra_data, []]
        # What SaveImage writes: the `prompt` hidden input always, then one chunk
        # per extra_pnginfo key (the editor's `workflow`, when a canvas queues it).
        chunks = {"prompt": text}
        for key, value in (extra_data.get("extra_pnginfo") or {}).items():
            chunks[key] = json.dumps(value)
        if FAIL_MARKER in text:
            return {
                "prompt": prompt,
                "outputs": {},
                "status": {"status_str": "error", "completed": False, "messages": [
                    ["execution_start", {"prompt_id": pid}],
                    ["execution_error", {
                        "prompt_id": pid, "node_id": "3", "node_type": "KSampler",
                        "exception_message": FAIL_MESSAGE,
                        "exception_type": "torch.OutOfMemoryError",
                    }],
                ]},
            }
        outputs: dict[str, dict] = {}
        refs: list[tuple[str, str, str]] = []
        for node_id, node in graph.items():
            kind = _OUTPUT_TYPES.get(node.get("class_type", "")) if isinstance(node, dict) else None
            if kind is None or str(node_id) in skip_outputs:
                continue
            self._image_counter += 1
            n = self._image_counter
            if kind == "output":
                prefix = (node.get("inputs") or {}).get("filename_prefix")
                subfolder, _, stem = (prefix if isinstance(prefix, str) and prefix else "ComfyUI").rpartition("/")
                filename = f"{stem}_{n:05d}_.png"
            else:
                subfolder, filename = "", f"ComfyUI_temp_fake_{n:05d}_.png"
            color = ((n * 53) % 256, (n * 97) % 256, (n * 151) % 256)
            self.images[(filename, subfolder, kind)] = make_png(color, **chunks)
            refs.append((filename, subfolder, kind))
            outputs[str(node_id)] = {"images": [{"filename": filename, "subfolder": subfolder, "type": kind}]}
        if CORRUPT_MARKER in text and refs:
            self.images[refs[-1]] = b"not a png"
        return {
            "prompt": prompt,
            "outputs": outputs,
            "status": {"status_str": "success", "completed": True, "messages": [
                ["execution_start", {"prompt_id": pid}],
                ["execution_success", {"prompt_id": pid}],
            ]},
        }

    def _poll(self, pid: str) -> dict | None:
        """History for `pid`, advancing a prompt that is finishing by one poll."""
        if pid in self._finishing:
            left, entry = self._finishing[pid]
            if left > 0:
                self._finishing[pid][0] = left - 1
                return None
            del self._finishing[pid]
            self.running = [e for e in self.running if e[0] != pid]
            self.history[pid] = entry
        return self.history.get(pid)

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        if self.unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content) if request.content else None

        if method == "POST" and path == "/prompt":
            graph = body.get("prompt") or {}
            invalid = _validation_error(graph)
            if invalid is not None:
                return httpx.Response(400, json=invalid)
            if REJECT_MARKER in json.dumps(graph):
                return httpx.Response(400, json={
                    "error": {"type": "prompt_outputs_failed_validation",
                              "message": "Prompt outputs failed validation"},
                    "node_errors": {"4": {"class_type": "CheckpointLoaderSimple", "errors": [
                        {"message": REJECT_MESSAGE, "details": "ckpt_name: 'missing.safetensors' not in []"},
                    ]}},
                })
            node_errors: dict[str, dict] = {}
            if PARTIAL_MARKER in json.dumps(graph):
                # The last output fails validation. ComfyUI queues the outputs that
                # passed and answers 200 with the rest in node_errors — or 400 when
                # nothing passed.
                outs = [nid for nid, n in graph.items() if isinstance(n, dict) and n.get("class_type") in _OUTPUT_TYPES]
                dropped = outs[-1]
                node_errors[dropped] = {
                    "class_type": graph[dropped]["class_type"], "dependent_outputs": [dropped],
                    "errors": [{"message": PARTIAL_MESSAGE, "details": "images"}],
                }
                if len(outs) == 1:
                    return httpx.Response(400, json={
                        "error": {"type": "prompt_outputs_failed_validation",
                                  "message": "Prompt outputs failed validation"},
                        "node_errors": node_errors,
                    })
            cid = body.get("client_id")
            self.ours_outstanding_at_submit.append(len(self.queue_ids(cid)))
            pid = f"OURS-{len(self.submitted) + 1}"
            self.submitted.append(pid)
            number = len(self.submitted)
            if self.complete:
                entry = self._execute(
                    pid, number, graph, {**(body.get("extra_data") or {}), "client_id": cid},
                    skip_outputs=set(node_errors),
                )
                if self.polls_before_done > 0:
                    self.running.append([pid, cid])
                    self._finishing[pid] = [self.polls_before_done, entry]
                else:  # finished instantly: straight into history
                    self.history[pid] = entry
            elif self.ours_start_running and not self.running:
                self.running.append([pid, cid])
            else:
                self.pending.append([pid, cid])
            if self.down_after_submits is not None and len(self.submitted) >= self.down_after_submits:
                self.unreachable = True
            return httpx.Response(200, json={"prompt_id": pid, "number": number, "node_errors": node_errors})

        if method == "GET" and path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            entry = self._poll(pid)
            return httpx.Response(200, json={pid: entry} if entry is not None else {})

        if method == "GET" and path == "/history":
            max_items = int(request.url.params.get("max_items") or 0)
            items = list(self.history.items())
            if max_items > 0:
                items = items[-max_items:]
            return httpx.Response(200, json=dict(items))  # oldest first, as ComfyUI

        if method == "GET" and path == "/queue":
            if self.queue_status != 200:
                return httpx.Response(self.queue_status, text="boom")

            def items(entries):
                return [[n, pid, {}, {"client_id": cid}, []] for n, (pid, cid) in enumerate(entries)]

            return httpx.Response(200, json={
                "queue_running": items(self.running),
                "queue_pending": items(self.pending),
            })

        if method == "POST" and path == "/queue":
            if not isinstance(body, dict) or "clear" in body:
                self.violations.append(f"/queue body {body!r}")
                return httpx.Response(200)
            ids = list(body.get("delete") or [])
            self.delete_requests.append(ids)
            if self.honour_cleanup:
                for entry in [e for e in self.pending if e[0] in ids]:
                    self.pending.remove(entry)
                    self.deleted.append(entry[0])
            return httpx.Response(200)

        if method == "POST" and path == "/interrupt":
            pid = (body or {}).get("prompt_id") if isinstance(body, dict) else None
            if not pid:
                # What an old ComfyUI does with any interrupt — and a new one with a
                # body-less one: stop whatever is executing, whoever submitted it.
                self.violations.append("global /interrupt")
                if self.running:
                    stopped = self.running.pop(0)[0]
                    self._finishing.pop(stopped, None)
                    self.interrupts.append(stopped)
                return httpx.Response(200)
            if self.honour_cleanup and self.running and self.running[0][0] == pid:
                self._finishing.pop(pid, None)
                self.interrupts.append(self.running.pop(0)[0])
            return httpx.Response(200)

        if method == "POST" and path == "/free":
            self.frees.append(body if isinstance(body, dict) else {})
            return httpx.Response(200)

        if method == "GET" and path == "/view":
            p = request.url.params
            key = (p.get("filename", ""), p.get("subfolder", ""), p.get("type", "output"))
            if key in self.images:
                return httpx.Response(200, content=self.images[key], headers={"content-type": "image/png"})
            return httpx.Response(404)

        if path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "fake"}, "devices": []})
        return httpx.Response(404)


def transport(fake: FakeComfy) -> httpx.MockTransport:
    """The in-process adapter: hand this to `ComfyClient(url, transport=...)`."""
    return httpx.MockTransport(fake.handler)


def serve(fake: FakeComfy, host: str = "127.0.0.1", port: int = 0, quiet: bool = False) -> ThreadingHTTPServer:
    """The real-socket adapter: a stdlib HTTP server (not yet serving) that runs
    every request through `fake.handler` under one lock. Stdlib rather than
    uvicorn, which `requirements-ci.txt` does not install. Port 0 picks a free
    port; read it back from `server.server_address[1]`."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self):
            length = int(self.headers.get("Content-Length") or 0)
            content = self.rfile.read(length) if length else b""
            request = httpx.Request(
                self.command, f"http://{host}:{self.server.server_address[1]}{self.path}",
                headers={k: v for k, v in self.headers.items()}, content=content,
            )
            with lock:
                try:
                    response = fake.handler(request)
                except httpx.ConnectError:
                    # `unreachable`: drop the connection unanswered. Over a real
                    # socket the client sees a protocol error, not a ConnectError.
                    self.close_connection = True
                    return
                except Exception:
                    # A bug in the fake, not a ComfyUI answer: say so in the log.
                    response = httpx.Response(500, text=traceback.format_exc())
                    sys.stderr.write(response.text)
            data = response.content
            self.send_response(response.status_code)
            ctype = response.headers.get("content-type")
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _dispatch

        def log_message(self, format, *args):
            if not quiet:
                super().log_message(format, *args)

    class Server(ThreadingHTTPServer):
        # SO_REUSEADDR on Windows lets a second socket bind a port another process
        # already holds, so a stale server could answer the readiness probe.
        allow_reuse_address = sys.platform != "win32"

    return Server((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a fake ComfyUI for the e2e suite.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8198)
    parser.add_argument("--verbose", action="store_true", help="log every request (one poll a second per row)")
    args = parser.parse_args()
    fake = FakeComfy(canvas_running=False, complete=True, polls_before_done=1)
    server = serve(fake, args.host, args.port, quiet=not args.verbose)
    print(f"fake ComfyUI on http://{args.host}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
