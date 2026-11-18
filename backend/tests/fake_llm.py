"""A fake OpenAI-compatible LLM server, shared by pytest and the Playwright e2e suite.

One handler, three adapters, on the pattern of `fake_comfy.py`. `FakeLLM.handle`
holds all the behaviour and knows no HTTP library: it returns a `Plan`, a status,
headers and a list of parts (bytes, `Pause`, `Stall`), and each adapter plays the
parts its own way. That is what lets one handler stream both in-process and over a
socket.

- `openai_client()` — a real `AsyncOpenAI` whose `http_client` runs on
  `sdk_transport()`. The SDK is built on `httpx2`, so an `httpx` transport cannot
  reach it; the adapter builds its mock transport from whichever flavour the
  installed SDK uses. The `fake_llm` fixture in `conftest.py` patches
  `openai_compat_captioner.make_client` to return this client.
- `transport()` — an `httpx.MockTransport`, for code on plain `httpx`
  (`run_service._LLM_TRANSPORT`). Its handler is async: async clients only.
- `serve()` — a stdlib HTTP server on a real port, for the e2e suite, whose backend
  is a separate process (`python -m backend.tests.fake_llm --port 8197`).

**Self-contained on purpose.** This module imports only the stdlib and httpx at
import time, and `openai` lazily inside `openai_client()`; never `conftest`, which
imports the app. It is not named `test_*`, so pytest does not collect it.

What it models:

- `GET /models` and `POST /chat/completions`, with or without a `/v1` prefix, plain
  and `stream: true`. A stream is SSE `data:` chunks ending in `data: [DONE]`; with
  `stream_options.include_usage` a last chunk carries `usage` and empty `choices`,
  as OpenAI sends it. Usage is a deterministic estimate, one token per four
  characters of message text; image parts are accepted and not counted.
- **Recording.** Every request lands in `requests` (method, path, lower-cased
  headers, parsed JSON body), the record privacy tests read. `GET /_fake/requests`
  serves it to e2e specs; `/_fake/*` requests are not recorded themselves.
- **Scripted replies.** A test pushes `Reply` objects onto `replies`, used first in
  order. Otherwise a marker in the **last user message** picks a preset (`PRESETS`),
  so a browser spec chooses one through the prompt text alone: `[[fake:refusal]]`,
  `[[fake:empty]]`, `[[fake:length]]`, `[[fake:notes]]`, `[[fake:notes-malformed]]`,
  `[[fake:tool]]`, `[[fake:no-usage]]`, `[[fake:slow]]`, `[[fake:stall]]`,
  `[[fake:429]]`, `[[fake:500]]`, `[[fake:401]]`. No marker gives `FAKE_REPLY`. An
  unknown marker is a 400 naming it, so a typo fails loudly.
- **Slow and stalled replies, plain or streamed.** `Reply.chunk_delay` spaces a
  stream's chunks; a plain reply waits the same total before it answers. A stall
  sends nothing more (a stream: after `stall_after` content chunks; a plain reply:
  nothing at all) until the client gives up or `Reply.stall_seconds` pass. A stream
  then ends with no finish chunk and no `[DONE]`; a plain request's connection is
  dropped unanswered.
- **The client's read timeout is honoured in-process too.** A mock transport would
  otherwise ignore it, so a wait longer than the request's read timeout raises the
  transport's `ReadTimeout`, which the SDK reports as `APITimeoutError` — what a
  real socket gives.
- **How each chat completion ended** is appended to `endings`, the same way in both
  adapters: `done` (fully delivered), `client_gone` (the client cancelled, closed the
  stream, disconnected or timed out first) or `timeout` (a stall ran its course). A
  cancel test reads it to prove the stream was dropped.

What it does not model: the legacy `/completions` endpoint, `n > 1`, logprobs,
response formats (a JSON-mode request gets the scripted text as is), and any check of
the API key.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import select
import socket
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import httpx

FAKE_REPLY = "A plain test image, described by the fake LLM server."
DEFAULT_MODELS = ("fake-chat", "fake-vision")

REFUSAL_TEXT = "I'm sorry, but I can't help with that request."
LENGTH_TEXT = "The dataset has forty images, and the most pressing problem for your goal is"
NOTES_TEXT = (
    "The short captions are the main problem; I would note the resolution decision.\n\n"
    "```crucible-notes\n"
    "add decision: Dropped images under 768px; the LoRA trains at 1024.\n"
    "add open_item: Re-caption the twelve captions under five tokens.\n"
    "```\n"
)
NOTES_MALFORMED_TEXT = (
    "Here is what I would record.\n\n"
    "```crucible-notes\n"
    "add decison Dropped the blurry images\n"
    "remove open_item three\n"
    "???\n"
    "add\n"
)  # misspelt op, a word for a number, junk, an op with no text, and no closing fence

_MARKER_RE = re.compile(r"\[\[fake:([a-z0-9-]+)\]\]")
_MODELS_PATHS = ("/models", "/v1/models")
_CHAT_PATHS = ("/chat/completions", "/v1/chat/completions")


@dataclass
class Pause:
    """Wait this long before the next part."""
    seconds: float


@dataclass
class Stall:
    """Send nothing more until the client gives up or `seconds` pass, then end."""
    seconds: float


@dataclass
class Plan:
    """What one request is answered with, free of any HTTP library. A plain plan's
    parts are its waits followed by its body; a streamed plan's interleave them."""
    status: int
    headers: dict[str, str]
    parts: list = field(default_factory=list)  # bytes | Pause | Stall
    streaming: bool = False
    chat: bool = False  # a chat completion: its ending is recorded

    def body(self) -> bytes:
        return b"".join(p for p in self.parts if isinstance(p, bytes))

    def waits(self) -> list:
        return [p for p in self.parts if not isinstance(p, bytes)]


@dataclass
class ToolCall:
    name: str
    arguments: str = "{}"  # a JSON string, as the API carries it


@dataclass
class Reply:
    """One scripted answer to a chat completion."""
    text: str = FAKE_REPLY
    finish_reason: str = "stop"
    tool_calls: list[ToolCall] | None = None
    usage: bool = True  # False: no usage, even when the request asks for it
    status: int = 200
    error: dict | None = None  # the OpenAI-shaped body of a non-200
    chunk_size: int = 8  # characters per streamed content chunk
    chunk_delay: float = 0.0
    stall_after: int | None = None  # content chunks streamed before a stall (capped at all of them)
    stall_seconds: float = 60.0


def _error(status: int, message: str, type_: str, code: str | None) -> Reply:
    return Reply(status=status, error={"error": {"message": message, "type": type_, "param": None, "code": code}})


def _first_tool(body: dict) -> Reply:
    """Call the first tool the request offers, with empty arguments."""
    tools = body.get("tools") or []
    if not tools:
        return Reply(text="[[fake:tool]] needs a request that offers tools.")
    name = (tools[0].get("function") or {}).get("name") or "unnamed"
    return Reply(text="", tool_calls=[ToolCall(name)], finish_reason="tool_calls")


PRESETS: dict[str, Callable[[dict], Reply]] = {
    "refusal": lambda _b: Reply(text=REFUSAL_TEXT),
    "empty": lambda _b: Reply(text=""),
    "length": lambda _b: Reply(text=LENGTH_TEXT, finish_reason="length"),
    "notes": lambda _b: Reply(text=NOTES_TEXT),
    "notes-malformed": lambda _b: Reply(text=NOTES_MALFORMED_TEXT),
    "tool": _first_tool,
    "no-usage": lambda _b: Reply(usage=False),
    "slow": lambda _b: Reply(text=FAKE_REPLY + " It arrives slowly, a few characters at a time.",
                             chunk_delay=0.15),
    "stall": lambda _b: Reply(text=FAKE_REPLY, stall_after=2),
    "429": lambda _b: _error(429, "Rate limit reached (fake).", "rate_limit_exceeded", "rate_limit_exceeded"),
    "500": lambda _b: _error(500, "The server had an error (fake).", "server_error", None),
    "401": lambda _b: _error(401, "Incorrect API key provided (fake).", "invalid_request_error", "invalid_api_key"),
}


def _text_of(content) -> str:
    """The text of a message's `content`: a string, or the text parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _sse(payload) -> bytes:
    return f"data: {payload if isinstance(payload, str) else json.dumps(payload)}\n\n".encode()


class FakeLLM:
    def __init__(self, *, models: tuple[str, ...] = DEFAULT_MODELS):
        self.models = list(models)
        self.replies: deque[Reply] = deque()
        self.requests: list[dict] = []
        self.endings: list[str] = []
        self._count = 0

    # ── The handler ──────────────────────────────────────────────────────────

    def handle(self, method: str, path: str, headers: dict, content: bytes) -> Plan:
        if path.startswith("/_fake/"):
            if method == "GET" and path == "/_fake/requests":
                return self._json(200, self.requests)
            return self._json(404, {"error": {"message": f"no fake route {path}"}})
        try:
            body = json.loads(content) if content else None
        except ValueError:
            body = None
        self.requests.append({
            "method": method, "path": path,
            "headers": {k.lower(): v for k, v in headers.items()},
            "json": body,
        })
        if method == "GET" and path in _MODELS_PATHS:
            return self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "fake"} for m in self.models]})
        if method == "POST" and path in _CHAT_PATHS:
            return self._chat(body if isinstance(body, dict) else {})
        return self._json(404, {"error": {"message": f"fake LLM has no route {method} {path}",
                                          "type": "invalid_request_error", "param": None, "code": None}})

    def _pick(self, body: dict) -> Reply:
        if self.replies:
            return self.replies.popleft()
        last_user = next((m for m in reversed(body.get("messages") or [])
                          if isinstance(m, dict) and m.get("role") == "user"), None)
        found = _MARKER_RE.search(_text_of((last_user or {}).get("content")))
        if found is None:
            return Reply()
        preset = PRESETS.get(found.group(1))
        if preset is None:
            return _error(400, f"unknown fake marker [[fake:{found.group(1)}]]", "invalid_request_error", None)
        return preset(body)

    def _chat(self, body: dict) -> Plan:
        reply = self._pick(body)
        if reply.status != 200:
            return self._json(reply.status, reply.error or {"error": {"message": "fake error"}})
        self._count += 1
        cid = f"chatcmpl-fake-{self._count}"
        model = body.get("model") or self.models[0]
        prompt_text = "".join(_text_of(m.get("content")) for m in body.get("messages") or [] if isinstance(m, dict))
        usage = {
            "prompt_tokens": _tokens(prompt_text),
            "completion_tokens": _tokens(reply.text + "".join(t.arguments for t in reply.tool_calls or [])),
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        pieces = [reply.text[i:i + reply.chunk_size] for i in range(0, len(reply.text), reply.chunk_size)]

        if not body.get("stream"):
            message: dict = {"role": "assistant", "content": reply.text if not reply.tool_calls else (reply.text or None)}
            if reply.tool_calls:
                message["tool_calls"] = [
                    {"id": f"call_fake_{i}", "type": "function",
                     "function": {"name": t.name, "arguments": t.arguments}}
                    for i, t in enumerate(reply.tool_calls)]
            out = {"id": cid, "object": "chat.completion", "created": 0, "model": model,
                   "choices": [{"index": 0, "message": message, "finish_reason": reply.finish_reason}]}
            if reply.usage:
                out["usage"] = usage
            plan = self._json(200, out)
            if reply.stall_after is not None:
                plan.parts.insert(0, Stall(reply.stall_seconds))
            elif reply.chunk_delay and len(pieces) > 1:
                plan.parts.insert(0, Pause(reply.chunk_delay * (len(pieces) - 1)))
            plan.chat = True
            return plan

        def chunk(delta: dict, finish: str | None = None) -> bytes:
            return _sse({"id": cid, "object": "chat.completion.chunk", "created": 0, "model": model,
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})

        parts: list = [chunk({"role": "assistant", "content": ""})]
        stall_at = None if reply.stall_after is None else min(reply.stall_after, len(pieces))
        for n, piece in enumerate(pieces):
            if n == stall_at:
                break
            if reply.chunk_delay and n:
                parts.append(Pause(reply.chunk_delay))
            parts.append(chunk({"content": piece}))
        if stall_at is not None:  # also when there was no text to stream first
            parts.append(Stall(reply.stall_seconds))
            return Plan(200, {"content-type": "text/event-stream"}, parts, streaming=True, chat=True)
        for i, t in enumerate(reply.tool_calls or []):
            parts.append(chunk({"tool_calls": [{"index": i, "id": f"call_fake_{i}", "type": "function",
                                                "function": {"name": t.name, "arguments": ""}}]}))
            half = len(t.arguments) // 2
            for frag in (t.arguments[:half], t.arguments[half:]):
                parts.append(chunk({"tool_calls": [{"index": i, "function": {"arguments": frag}}]}))
        parts.append(chunk({}, reply.finish_reason))
        if reply.usage and (body.get("stream_options") or {}).get("include_usage"):
            parts.append(_sse({"id": cid, "object": "chat.completion.chunk", "created": 0, "model": model,
                               "choices": [], "usage": usage}))
        parts.append(_sse("[DONE]"))
        return Plan(200, {"content-type": "text/event-stream"}, parts, streaming=True, chat=True)

    @staticmethod
    def _json(status: int, payload) -> Plan:
        return Plan(status, {"content-type": "application/json"}, [json.dumps(payload).encode()])

    # ── In-process adapters ──────────────────────────────────────────────────

    async def _wait(self, part, read: float | None, mod, request) -> bool:
        """Play one `Pause` or `Stall` under the request's read timeout. Raises the
        transport's `ReadTimeout` when the client would give up first; returns True
        when a stall ran its course."""
        if read is not None and part.seconds > read:
            await asyncio.sleep(read)
            raise mod.ReadTimeout("fake LLM: nothing arrived within the read timeout", request=request)
        await asyncio.sleep(part.seconds)
        return isinstance(part, Stall)

    async def _stream_body(self, plan: Plan, read: float | None, mod, request):
        ending = "client_gone"  # unless the loop below says otherwise
        last = len(plan.parts) - 1
        try:
            for n, part in enumerate(plan.parts):
                if isinstance(part, bytes):
                    if n == last:
                        # Handed over in full: the SDK stops at `[DONE]` and closes
                        # the response without resuming this generator.
                        ending = "done"
                    yield part
                elif await self._wait(part, read, mod, request):
                    ending = "timeout"
                    return
            ending = "done"
        finally:
            # Also reached on a cancel, an `aclose()` and a read timeout.
            self.endings.append(ending)

    def _mock_transport(self, mod):
        async def handler(request):
            await request.aread()
            plan = self.handle(request.method, request.url.path, dict(request.headers), request.content)
            read = (request.extensions.get("timeout") or {}).get("read")
            if plan.streaming:
                return mod.Response(plan.status, headers=plan.headers,
                                    content=self._stream_body(plan, read, mod, request))
            if plan.chat:
                ending = "client_gone"
                try:
                    for part in plan.waits():
                        if await self._wait(part, read, mod, request):
                            ending = "timeout"
                            raise mod.RemoteProtocolError("fake LLM: stalled, then dropped the connection",
                                                          request=request)
                    ending = "done"
                finally:
                    self.endings.append(ending)
            return mod.Response(plan.status, headers=plan.headers, content=plan.body())
        return mod.MockTransport(handler)

    def transport(self) -> httpx.MockTransport:
        """For code on plain `httpx`. The handler is async: async clients only."""
        return self._mock_transport(httpx)

    def sdk_transport(self):
        """A mock transport of the `httpx` flavour the installed OpenAI SDK uses."""
        return self._mock_transport(_sdk_httpx())

    def openai_client(self, *, api_key: str = "test-key", timeout: float = 30.0, base_url: str = "http://fake-llm.test/v1"):
        """A real `AsyncOpenAI` wired to this fake, set up as `make_client` sets one up."""
        from openai import AsyncOpenAI

        mod = _sdk_httpx()
        return AsyncOpenAI(
            base_url=base_url, api_key=api_key or "no-key", timeout=timeout, max_retries=0,
            http_client=mod.AsyncClient(transport=self.sdk_transport()),
        )


def _sdk_httpx():
    """The `httpx` module the OpenAI SDK's own client class is built on: `httpx2`
    for current releases, `httpx` for older ones."""
    import openai

    for cls in getattr(openai, "DefaultAsyncHttpxClient", type).__mro__[1:]:
        root = cls.__module__.split(".")[0]
        if root in ("httpx", "httpx2"):
            return importlib.import_module(root)
    return httpx


# ── Socket adapter ───────────────────────────────────────────────────────────

def _client_gone(conn: socket.socket) -> bool:
    """True once the peer has closed its end: readable, and a peek reads nothing."""
    try:
        readable, _, _ = select.select([conn], [], [], 0)
        return bool(readable) and conn.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


def _sit_out(conn: socket.socket, part) -> str | None:
    """Wait out a `Pause` or `Stall`, watching for the client to leave. Returns
    `client_gone`, `timeout` (a stall ran its course), or None (a pause ended)."""
    deadline = time.monotonic() + part.seconds
    while time.monotonic() < deadline:
        if _client_gone(conn):
            return "client_gone"
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return "timeout" if isinstance(part, Stall) else None


def serve(fake: FakeLLM, host: str = "127.0.0.1", port: int = 0, quiet: bool = False) -> ThreadingHTTPServer:
    """The real-socket adapter: a stdlib HTTP server (not yet serving). Unlike
    `fake_comfy.serve`, the lock covers only `handle` — popping a reply and
    recording the request — and recording an ending, never a wait or a write, so a
    stalled reply does not block other requests. Port 0 picks a free port; read it
    back from `server.server_address[1]`."""
    lock = threading.Lock()

    def ended(plan: Plan, how: str) -> None:
        if plan.chat:
            with lock:
                fake.endings.append(how)

    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self):
            length = int(self.headers.get("Content-Length") or 0)
            content = self.rfile.read(length) if length else b""
            path = self.path.split("?", 1)[0]
            with lock:
                try:
                    plan = fake.handle(self.command, path, dict(self.headers.items()), content)
                except Exception:
                    # A bug in the fake, not an LLM answer: say so in the log.
                    text = traceback.format_exc()
                    sys.stderr.write(text)
                    plan = Plan(500, {"content-type": "text/plain"}, [text.encode()])
            try:
                if plan.streaming:
                    ended(plan, self._stream(plan))
                    return
                for part in plan.waits():
                    how = _sit_out(self.connection, part)
                    if how is not None:  # gone, or a stall ran out: drop it unanswered
                        self.close_connection = True
                        ended(plan, how)
                        return
                data = plan.body()
                self.send_response(plan.status)
                for k, v in plan.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                ended(plan, "done")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
                ended(plan, "client_gone")

        def _stream(self, plan: Plan) -> str:
            """Write a streamed plan close-delimited (no Content-Length; the
            connection ends the body) and say how it ended."""
            self.send_response(plan.status)
            for k, v in plan.headers.items():
                self.send_header(k, v)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            for part in plan.parts:
                if isinstance(part, bytes):
                    self.wfile.write(part)
                    self.wfile.flush()
                else:
                    how = _sit_out(self.connection, part)
                    if how is not None:
                        return how
            return "done"

        do_GET = do_POST = do_DELETE = _dispatch

        def log_message(self, format, *args):
            if not quiet:
                super().log_message(format, *args)

    class Server(ThreadingHTTPServer):
        # SO_REUSEADDR on Windows lets a second socket bind a port another process
        # already holds, so a stale server could answer the readiness probe.
        allow_reuse_address = sys.platform != "win32"

    return Server((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a fake OpenAI-compatible LLM for the e2e suite.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8197)
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args()
    server = serve(FakeLLM(), args.host, args.port, quiet=not args.verbose)
    print(f"fake LLM on http://{args.host}:{server.server_address[1]}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
