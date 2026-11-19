"""One streamed chat completion against an OpenAI-compatible server (M12 phase 2): the
app's first streaming LLM call.

The client comes from `openai_compat_captioner.make_client`, so it has `max_retries=0`
(PM-023) and the `fake_llm` fixture can stand in for it. This module only reads the
stream; who holds the provider's slot, records usage and stores the reply is
`services/chat_runner.py`.

**Two time limits instead of one total.** A provider's `timeout_s` is tuned for one
caption; a chat reply can stream for minutes and still be healthy. So the limits here are
how long the first piece of text may take (`first_token_s`, which covers a slow prompt
evaluation) and how long the stream may go quiet after that (`idle_s`). Each is applied to
one step of the stream with `asyncio.wait_for`.

Errors are sorted by `prompt_generator._classify` (`transient`, `rejected`, `fatal`), plus
`timeout` for the two limits here. See docs/dev/analyst/sending.md.

**Tool calls (M12 phase 3).** With `tools`, the request offers them and the stream's
`tool_calls` deltas are put back together by `ToolCallBuffer`; a tool-call piece counts as
output for the time limits, like text. See docs/dev/analyst/tools.md.
"""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.ml.prompt_generator import _classify, _status


class ChatStreamError(Exception):
    """The call failed. `kind` sorts it; `opened` says whether the server had already
    accepted the request (and so may have billed for it); `refused` says the server
    answered with an HTTP error status, so it did not run the request."""

    def __init__(self, kind: str, message: str, *, opened: bool, refused: bool = False):
        super().__init__(message)
        self.kind = kind
        self.opened = opened
        self.refused = refused


@dataclass
class ToolCallOut:
    """One tool call the model made, put back together from its stream pieces.
    `arguments` is the raw JSON text as sent; the caller parses and validates it."""
    id: str
    name: str
    arguments: str


@dataclass
class StreamResult:
    finish_reason: str | None
    # From the server's `usage`, or None when it reported none.
    tokens_in: int | None
    tokens_out: int | None
    tool_calls: list[ToolCallOut] = field(default_factory=list)


class ToolCallBuffer:
    """Puts streamed `delta.tool_calls` pieces back together.

    The OpenAI stream sends each call's id and name in its first piece and the arguments
    in fragments after, all keyed by `index`. Servers differ at the edges, so: a piece
    with no `index` continues the last call, unless it carries a different id, which
    starts a new one; a name sent again whole is not doubled; a call that never got an
    id is given `call_<n>`, since the next request must name it to answer it."""

    def __init__(self) -> None:
        self._calls: dict[int, dict] = {}

    def add(self, pieces) -> None:
        for tc in pieces or []:
            index = getattr(tc, "index", None)
            tc_id = getattr(tc, "id", None)
            if index is None:
                last = max(self._calls) if self._calls else None
                if last is None:
                    index = 0
                elif tc_id and self._calls[last]["id"] not in (None, tc_id):
                    index = last + 1
                else:
                    index = last
            entry = self._calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
            if tc_id and not entry["id"]:
                entry["id"] = tc_id
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn is not None else None
            if name and name != entry["name"]:
                entry["name"] = entry["name"] + name
            args = getattr(fn, "arguments", None) if fn is not None else None
            if args:
                entry["arguments"] += args

    def calls(self) -> list["ToolCallOut"]:
        return [
            ToolCallOut(id=c["id"] or f"call_{n}", name=c["name"], arguments=c["arguments"])
            for n, c in sorted(self._calls.items())
        ]


def usage_tokens(usage) -> tuple[int | None, int | None]:
    """(input, output) tokens from a usage object.

    **Output is the larger of `completion_tokens` and `total_tokens - prompt_tokens`.**
    A reasoning model's hidden thinking is billed as output, and not every server counts
    it in `completion_tokens`: Gemini 2.5 Flash's OpenAI-compatible endpoint reported 57
    completion tokens with a total of 833 for a 16-token prompt (measured 2026-11-19), so
    reading `completion_tokens` alone under-counted a budget roughly tenfold. OpenAI itself
    includes reasoning in `completion_tokens`, where the two agree."""
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if prompt is not None and total is not None:
        hidden = total - prompt
        completion = hidden if completion is None else max(completion, hidden)
    return prompt, completion


async def stream_chat(
    client,
    *,
    model: str,
    messages: list[dict],
    max_tokens: int,
    first_token_s: float,
    idle_s: float,
    on_text: Callable[[str], None],
    on_open: Callable[[], None] | None = None,
    on_send: Callable[[], None] | None = None,
    tools: list[dict] | None = None,
) -> StreamResult:
    """Stream one reply, handing each piece of text to `on_text` as it arrives.

    `on_send` runs just before the request goes out, `on_open` once the server has
    accepted it. With `tools`, the request offers them (`tool_choice="auto"`) and the
    result carries any calls the model made. Raises `ChatStreamError` on a failure or a
    time limit; a cancel propagates as `CancelledError`, with the stream closed either way.
    """
    if on_send is not None:
        on_send()
    extra = {"tools": tools, "tool_choice": "auto"} if tools else {}
    try:
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                **extra,
            ),
            first_token_s,
        )
    except asyncio.TimeoutError:
        raise ChatStreamError("timeout", f"The provider did not answer within {first_token_s:g} s.", opened=False)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — sorted, never swallowed
        raise ChatStreamError(
            _classify(exc), str(exc) or type(exc).__name__, opened=False, refused=_status(exc) is not None,
        ) from exc

    if on_open is not None:
        on_open()
    finish_reason = None
    tokens_in = tokens_out = None
    # Text or a tool-call piece: either ends the first-token limit, so a reply that is
    # only tool calls runs on the idle limit like any other once it has started.
    got_output = False
    buffer = ToolCallBuffer()
    chunks = stream.__aiter__()
    try:
        while True:
            limit = idle_s if got_output else first_token_s
            try:
                chunk = await asyncio.wait_for(chunks.__anext__(), limit)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                what = "went quiet for" if got_output else "sent nothing within"
                raise ChatStreamError("timeout", f"The reply {what} {limit:g} s.", opened=True)
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                tokens_in, tokens_out = usage_tokens(usage)
            for choice in chunk.choices or []:
                delta = choice.delta
                piece = getattr(delta, "content", None) if delta is not None else None
                if piece:
                    got_output = True
                    on_text(piece)
                calls = getattr(delta, "tool_calls", None) if delta is not None else None
                if calls:
                    got_output = True
                    buffer.add(calls)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
    except (ChatStreamError, asyncio.CancelledError):
        raise
    except Exception as exc:  # noqa: BLE001 — a dropped connection mid-stream
        raise ChatStreamError(_classify(exc), str(exc) or type(exc).__name__, opened=True) from exc
    finally:
        try:
            await stream.close()
        except Exception:  # noqa: BLE001 — closing a broken stream is best effort
            pass
    return StreamResult(
        finish_reason=finish_reason, tokens_in=tokens_in, tokens_out=tokens_out, tool_calls=buffer.calls(),
    )
