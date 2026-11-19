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
    `arguments` is the raw JSON text as sent; the caller parses and validates it.
    `extra_content` is whatever the server attached to the call beyond the standard
    fields, which the next request must send back with it: Gemini's OpenAI-compatible
    endpoint carries a thought signature there (`extra_content.google.thought_signature`)
    and refuses a tool answer that drops it."""
    id: str
    name: str
    arguments: str
    extra_content: dict | None = None

    def as_message_call(self) -> dict:
        """The call as an assistant message's `tool_calls` entry, for the next request."""
        out = {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.arguments}}
        if self.extra_content:
            out["extra_content"] = self.extra_content
        return out


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
    in fragments after, all keyed by `index`. Servers differ at the edges, and the rules
    below lean towards keeping two calls apart, since two calls merged into one give
    arguments that are not JSON:

    - A piece whose id differs from its call's id starts a new call, whatever its
      `index` says (some servers put every parallel call on index 0).
    - A piece with no `index` continues the last call, unless it starts a new one: a
      different id, or, with no id, a name when the last call already has its name and
      some arguments (a server that sends each call whole in one piece), unless the piece
      only repeats that name.
    - A name sent again whole is not doubled, and a name sent growing (`cru`, then
      `crucible_echo`) is replaced, not appended.
    - A call that never got an id is given `call_<n>`, since the next request has to
      name the call it answers.
    """

    def __init__(self) -> None:
        self._calls: list[dict] = []
        # The server's `index` -> the position in `_calls` it currently feeds.
        self._by_index: dict[int, int] = {}

    def _new(self, index: int | None) -> dict:
        entry = {"id": None, "name": "", "arguments": "", "extra_content": None}
        self._calls.append(entry)
        if index is not None:
            self._by_index[index] = len(self._calls) - 1
        return entry

    def add(self, pieces) -> None:
        for tc in pieces or []:
            index = getattr(tc, "index", None)
            tc_id = getattr(tc, "id", None) or None
            fn = getattr(tc, "function", None)
            name = (getattr(fn, "name", None) if fn is not None else None) or None
            args = (getattr(fn, "arguments", None) if fn is not None else None) or ""
            if index is not None:
                pos = self._by_index.get(index)
                entry = self._calls[pos] if pos is not None else None
            else:
                entry = self._calls[-1] if self._calls else None
                repeat = name == entry["name"] and not args if entry is not None else False
                if entry is not None and tc_id is None and name and entry["name"] and entry["arguments"] and not repeat:
                    entry = None
            if entry is None or (tc_id and entry["id"] and tc_id != entry["id"]):
                entry = self._new(index)
            if tc_id and not entry["id"]:
                entry["id"] = tc_id
            if name:
                if not entry["name"] or name.startswith(entry["name"]):
                    entry["name"] = name
                elif not entry["name"].startswith(name):
                    entry["name"] += name
            entry["arguments"] += args
            extra = getattr(tc, "model_extra", None)
            if isinstance(extra, dict) and isinstance(extra.get("extra_content"), dict):
                entry["extra_content"] = extra["extra_content"]

    def calls(self) -> list[ToolCallOut]:
        return [
            ToolCallOut(id=c["id"] or f"call_{n}", name=c["name"], arguments=c["arguments"],
                        extra_content=c["extra_content"])
            for n, c in enumerate(self._calls)
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
    tool_choice: str = "auto",
) -> StreamResult:
    """Stream one reply, handing each piece of text to `on_text` as it arrives.

    `on_send` runs just before the request goes out, `on_open` once the server has
    accepted it. With `tools`, the request offers them under `tool_choice` ("auto", or
    "none" to send them without letting the model call one) and the result carries any
    calls the model made. Raises `ChatStreamError` on a failure or a
    time limit; a cancel propagates as `CancelledError`, with the stream closed either way.
    """
    if on_send is not None:
        on_send()
    extra = {"tools": tools, "tool_choice": tool_choice} if tools else {}
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
