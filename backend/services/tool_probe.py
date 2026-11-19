"""The analyst's tool-call probe (M12 phase 3b): does this model, on this server, handle
tool calls?

One scripted round trip, two calls. The first offers a single tool, `crucible_echo`, and
asks the model to call it with a fixed word; the second sends the tool's answer, a random
check code, and passes when the reply repeats the code. That is the whole loop the chat
will run, so a model that passes can be trusted with it, and one that answers in text,
calls the wrong tool, garbles the arguments or ignores the result cannot.

**A verdict or nothing.** A pass or a fail is stored per model in
`openai_providers.tools_probe`, and so is a server that refuses the first request, the one
offering tools, with a `rejected` 4xx. That 4xx is not always about tools (Gemini answers a
bad key with a 400), so the stored detail carries the server's message. Anything else says
nothing about tool support and is reported as `ProbeInconclusive`, storing nothing: a
timeout, a 5xx, a 401/403/404, a refusal of the tool's answer, a reply cut off at Max
tokens, or the whole probe passing `PROBE_TOTAL_S`.

**It is a real call**, so it follows the chat's rules: it holds one of the provider's
shared slots for each call (`llm_limiter`), builds its client with `make_client`, uses the
chat's two time limits, records each call in `llm_usage` (with no chat, so a billed one
counts toward today's budget) under the same rule a reply follows, and is refused before
it starts when a billed provider's daily budget could not cover it. The key never leaves
the task; error text goes through `chat_runner.scrub`. See docs/dev/analyst/tools.md.
"""
import asyncio
import json
import logging
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ml import openai_compat_captioner
from backend.ml.chat_stream import ChatStreamError, StreamResult, stream_chat
from backend.models import LlmUsage, OpenAIProvider
from backend.schemas.openai_provider import ToolProbeOut, provider_is_billed
from backend.services import chat_runner, llm_limiter
from backend.services.threshold_service import get_thresholds

logger = logging.getLogger(__name__)

PROBE_TOOL_NAME = "crucible_echo"
PROBE_WORD = "ember"
PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": PROBE_TOOL_NAME,
        "description": "Echoes a word back with a check code. Call it when asked to.",
        # Shaped like the analyst's own tools (`analyst_tools.TOOLS`): a nested object with
        # no required fields, integer bounds, an enum and a length cap. A server that
        # accepts this schema accepts theirs, so a pass is not undone by the first real
        # request. The model need not fill `options`.
        "parameters": {
            "type": "object",
            "properties": {
                "word": {"type": "string", "maxLength": 40, "description": "The word to echo."},
                "options": {
                    "type": "object",
                    "properties": {
                        "repeat": {"type": "integer", "minimum": 1, "maximum": 3,
                                   "description": "How many times to echo it (default 1)."},
                        "case": {"type": "string", "enum": ["as_is", "upper"],
                                 "description": "How to write it (default as_is)."},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
            "required": ["word"],
            "additionalProperties": False,
        },
    },
}
PROBE_PROMPT = (
    "This is a short test of tool calling. Call the crucible_echo tool with the word "
    f"'{PROBE_WORD}'. It answers with a check code. Then reply with that check code and "
    "nothing else."
)
PASSED = "Passed: the model called the tool and used its answer."
# The most the whole probe may take. The chat's limits are per chunk, and a reasoning
# model's hidden thinking arrives as chunks that are not text, so without this a slow
# model keeps a "Check tool support" click waiting until Max tokens runs out: Qwen 3.8
# 27B on a busy GPU took 723 s and still reached no verdict (2026-11-19). A module
# constant so a test can shorten it.
PROBE_TOTAL_S = 300.0

# (provider id, model) pairs being probed, so a double-click runs one probe, not two.
_probing: set[tuple[str, str]] = set()


class ProbeInconclusive(Exception):
    """The probe could not reach a verdict; nothing is stored. The message is safe to
    show: it has been scrubbed of the key."""


async def _record_usage(cfg, result: StreamResult | None, prompt_chars: int, text: str) -> None:
    from backend.database import AsyncSessionLocal

    tokens_in = result.tokens_in if result is not None else None
    tokens_out = result.tokens_out if result is not None else None
    estimated = tokens_in is None or tokens_out is None
    if tokens_in is None:
        tokens_in = chat_runner.estimate_tokens(prompt_chars)
    if tokens_out is None:
        tokens_out = chat_runner.estimate_tokens(len(text))
    async with AsyncSessionLocal() as s:
        s.add(LlmUsage(
            day=chat_runner.local_day(), chat_id=None, message_id=None, provider_id=cfg.id,
            tokens_in=tokens_in, tokens_out=tokens_out, estimated=estimated, billed=cfg.billed,
        ))
        await s.commit()


def _chars(messages: list[dict]) -> int:
    return sum(len(json.dumps(m)) for m in messages) + len(json.dumps(PROBE_TOOL))


async def _round(cfg, model: str, messages: list[dict]) -> tuple[str, StreamResult]:
    """One streamed call offering the probe tool. Its usage is recorded however it
    ends, under the rule a chat reply follows (`chat_runner._execute`)."""
    parts: list[str] = []
    opened = sent = refused = False
    result: StreamResult | None = None

    def on_send() -> None:
        nonlocal sent
        sent = True

    def on_open() -> None:
        nonlocal opened
        opened = True

    try:
        async with llm_limiter.slot(cfg.id, cfg.max_concurrent):
            client = openai_compat_captioner.make_client(cfg.base_url, cfg.api_key, cfg.timeout_s)
            if client is None:
                raise ChatStreamError("fatal", "The openai package is not installed on this server.", opened=False)
            try:
                result = await stream_chat(
                    client, model=model, messages=messages, max_tokens=cfg.max_tokens,
                    first_token_s=chat_runner.FIRST_TOKEN_S, idle_s=chat_runner.IDLE_S,
                    on_text=parts.append, on_open=on_open, on_send=on_send, tools=[PROBE_TOOL],
                )
            finally:
                await client.close()
    except ChatStreamError as exc:
        opened = opened or exc.opened
        refused = exc.refused
        raise
    finally:
        if opened or parts or (cfg.billed and sent and not refused):
            try:
                await asyncio.shield(_record_usage(cfg, result, _chars(messages), "".join(parts)))
            except Exception:  # noqa: BLE001 — never mask the probe's own outcome
                logger.error("Tool probe: could not record usage for provider %s", cfg.id, exc_info=True)
    return "".join(parts), result


def _word(arguments: str) -> str | None:
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        return None
    word = args.get("word") if isinstance(args, dict) else None
    return word.strip() if isinstance(word, str) else None


async def _verdict(cfg, model: str) -> tuple[bool, str]:
    """Run the two calls; (ok, detail) for a verdict, or `ProbeInconclusive`."""
    messages: list[dict] = [{"role": "user", "content": PROBE_PROMPT}]
    try:
        text, first = await _round(cfg, model, messages)
    except ChatStreamError as exc:
        if exc.kind == "rejected":
            return False, "The server refused a request that offers tools: " + chat_runner.scrub(str(exc), cfg.api_key)
        raise ProbeInconclusive(f"The call failed ({exc.kind}): {chat_runner.scrub(str(exc), cfg.api_key)}") from exc
    if first.finish_reason == "length" and not first.tool_calls:
        raise ProbeInconclusive(
            "The reply was cut off by the provider's Max tokens before it called the tool. "
            "Raise Max tokens and check again.")
    if not first.tool_calls:
        return False, "The model answered in text instead of calling the tool."
    echo = next((c for c in first.tool_calls if c.name == PROBE_TOOL_NAME), None)
    if echo is None:
        return False, f"The model called a tool that was not offered ({first.tool_calls[0].name[:60]!r})."
    word = _word(echo.arguments)
    if word is None and first.finish_reason == "length":
        # Cut off inside the arguments: Max tokens ran out, which says nothing about tools.
        raise ProbeInconclusive(
            "The reply was cut off by the provider's Max tokens while it called the tool. "
            "Raise Max tokens and check again.")
    if word is None:
        return False, "The model called the tool, but its arguments were not the JSON the tool asks for."
    if word.lower() != PROBE_WORD:
        return False, "The model called the tool with the wrong word."

    code = secrets.token_hex(3)
    # Every named call is sent back with its answer, extra fields included (Gemini's
    # thought signature, `ToolCallOut.extra_content`); a call with no name is left out,
    # since a strict server refuses one.
    named = [c for c in first.tool_calls if c.name]
    messages.append({
        "role": "assistant",
        "content": text or None,
        "tool_calls": [c.as_message_call() for c in named],
    })
    for c in named:
        answer = f"Check code: {code}" if c is echo else "No such tool."
        messages.append({"role": "tool", "tool_call_id": c.id, "content": answer})
    try:
        reply, second = await _round(cfg, model, messages)
    except ChatStreamError as exc:
        # Even a `rejected` here is no verdict: the model did call the tool, and a refusal
        # of the answer is as likely to be about how Crucible sent it as about the model.
        raise ProbeInconclusive(
            f"The server refused the tool's answer ({exc.kind}): {chat_runner.scrub(str(exc), cfg.api_key)}"
        ) from exc
    if code in reply:
        return True, PASSED
    if second.finish_reason == "length":
        raise ProbeInconclusive(
            "The answer was cut off by the provider's Max tokens. Raise Max tokens and check again.")
    if second.tool_calls:
        return False, "The model called the tool again instead of using its answer."
    return False, "The model did not use the tool's answer in its reply."


async def probe(db: AsyncSession, provider_id: str, model: str | None) -> ToolProbeOut:
    """Probe one model on one provider and store the verdict. Raises HTTPException:
    404 no provider, 422 no model, 409 a probe of it already running or a billed daily
    budget that could not cover it, 502 no verdict (`ProbeInconclusive`)."""
    row = await db.get(OpenAIProvider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    model = (model or row.default_model or "").strip()
    if not model:
        raise HTTPException(status_code=422, detail="Choose a model to check: this provider has no default model.")
    cfg = chat_runner.provider_snapshot(row)
    key = (row.id, model)
    if key in _probing:
        raise HTTPException(status_code=409, detail="A check of this model is already running.")
    # Taken before the first await, so a double-click cannot slip a second probe in while
    # the first is still reading the budget.
    _probing.add(key)
    try:
        if cfg.billed:
            # Both calls at their most: the prompt (twice, plus the tool's answer) and two
            # full-length replies.
            estimate = 2 * (chat_runner.estimate_tokens(_chars([{"content": PROBE_PROMPT}]) * 2) + cfg.max_tokens)
            settings = await get_thresholds(db)
            used = await chat_runner.billed_used(db)
            if used + estimate > settings.analyst_daily_budget_tokens:
                raise HTTPException(status_code=409, detail=(
                    f"Checking could use about {estimate:,} tokens, and {used:,} of today's "
                    f"{settings.analyst_daily_budget_tokens:,} are used. Raise the daily budget in "
                    "Settings → Analyst, or check tomorrow."))
        # End the read transaction: the calls can take minutes.
        await db.commit()
        try:
            ok, detail = await asyncio.wait_for(_verdict(cfg, model), PROBE_TOTAL_S)
        except asyncio.TimeoutError:
            raise ProbeInconclusive(
                f"The model took longer than {PROBE_TOTAL_S:g} s. If it is a reasoning model, "
                "it may still handle tools: check again when the server is less busy.") from None
    except ProbeInconclusive as exc:
        logger.info("Tool probe: provider %s, model %s: no verdict", cfg.id, model)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        _probing.discard(key)

    entry = {"ok": ok, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "detail": detail}
    logger.info("Tool probe: provider %s, model %s: %s", cfg.id, model, "passed" if ok else "failed")
    row = await db.get(OpenAIProvider, provider_id, populate_existing=True)
    # Store only against the server that was probed: an edit to the address or to
    # whether it bills while the probe ran clears the results, and this one is stale.
    if row is not None and (row.base_url, provider_is_billed(row)) == (cfg.base_url, cfg.billed):
        probes = dict(row.tools_probe or {})  # a copy: an in-place edit is not seen as a change
        probes[model] = entry
        row.tools_probe = probes
        await db.commit()
    return ToolProbeOut(model=model, **entry)
