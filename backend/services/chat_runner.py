"""Sending an analyst chat message and streaming the reply (M12 phase 2b).

The order of a send, each step for a reason:

1. **Refusals first, before anything is stored**: no provider or model, a reply still
   being written for this chat, or a billed provider's token budget that the estimate
   would pass (the 409 names the numbers).
2. **Both rows are stored before the call**: the user's message, then an assistant row
   with `status="partial"` carrying the manifest of what its context sent. A reload
   during the stream finds the reply so far; a crash leaves a `partial` row that the
   startup sweep (`mark_interrupted`) turns into `error: interrupted`.
3. **A background task owns the call**, not the request handler, so a closed tab or a
   dropped connection lets the reply finish. It holds one of the provider's shared slots
   (`llm_limiter`), streams through `ml.chat_stream`, and writes the text so far about
   once a second.
4. **Usage is committed before the reply**: the `llm_usage` row first, in its own
   commit, then the message. A failed or cancelled call that reached the server still
   records what it used (the paid-call cap's rule, `docs/dev/comfyui/run-budgets.md`),
   estimated from characters when the server reported nothing.

**Tools (M12 phase 3d).** When the chat's model passed the tool-call probe, a reply may
take several calls to the model: each round that ends in tool calls has its usage
committed at once, its calls run through `analyst_tools.run_tool` and stored as
`ChatToolCall` rows, and the results go back in the next call. A cap on rounds and on
result characters ends with one last call that may not use tools; the budget is checked
again before every round. See docs/dev/analyst/tool-loop.md.

Live events go to whoever is listening, through one queue per listener: `start` (with
the text so far, and the tool calls so far, so a reconnect resumes), `delta`,
`tool_call`, `tool_result`, `usage`, then `done` or `error`. The
API key is loaded from the provider row at send time and never enters a log line, an
event or a stored row; an error message is scrubbed of it. See docs/dev/analyst/sending.md.
"""
import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ml import openai_compat_captioner
from backend.ml.chat_stream import ChatStreamError, stream_chat
from backend.models import Chat, ChatMessage, ChatToolCall, Dataset, LlmUsage, OpenAIProvider
from backend.schemas.chat import BudgetOut, ChatMessageOut, ToolCallOut
from backend.schemas.openai_provider import provider_is_billed
from backend.services import analyst_tools, health_service, llm_limiter, note_proposals
from backend.services.analyst_context import CHARS_PER_TOKEN, build_context, render_tool_result
from backend.services.threshold_service import get_thresholds

logger = logging.getLogger(__name__)

# How long the first text may take (a long prompt on a slow local model), and how long
# the stream may then go quiet. Module constants so a test can shorten them.
FIRST_TOKEN_S = 180.0
IDLE_S = 60.0
# How often the reply so far is written to its row while it streams.
FLUSH_EVERY_S = 1.0
# The Related findings strip: the model's valid references first, then the most severe
# open findings until there are this many, never more than `RELATED_MAX`.
RELATED_MIN = 3
RELATED_MAX = 6
TITLE_MAX = 60
# The tool loop (phase 3d). Rounds that may call tools, then one that may not; that last
# one also comes once the turn's tool results reach `TOOL_TURN_MAX_CHARS`. Calls past
# `MAX_CALLS_PER_ROUND` in one round are answered with an error, not run. Module
# constants so a test can shorten them.
MAX_TOOL_ROUNDS = 4
TOOL_TURN_MAX_CHARS = 24_000
MAX_CALLS_PER_ROUND = 5
# The most of a call's raw arguments stored on its row.
ARGUMENTS_MAX_CHARS = 20_000

# A fixed pattern, so stdlib `re` is safe (CLAUDE.md § client-supplied regex).
FINDING_REF_RE = re.compile(r"\[\[finding:([a-z0-9_]+)\]\]")

INTERRUPTED_DETAIL = "Crucible stopped before the reply finished."
TERMINAL_EVENTS = ("done", "error")


@dataclass
class _Provider:
    """What the call needs from the provider row, read once at send time."""
    id: str
    base_url: str
    api_key: str = field(repr=False)  # never in a repr, a log line or a traceback's locals
    timeout_s: float = 0.0
    max_tokens: int = 0
    max_concurrent: int = 1
    billed: bool = False


def provider_snapshot(provider: OpenAIProvider) -> _Provider:
    """What a call needs from the provider row, read once before it starts. The tool-call
    probe (`services/tool_probe.py`) takes the same snapshot."""
    return _Provider(
        id=provider.id, base_url=provider.base_url, api_key=provider.api_key or "",
        timeout_s=float(provider.timeout_s), max_tokens=int(provider.max_tokens),
        max_concurrent=int(provider.max_concurrent), billed=provider_is_billed(provider),
    )


@dataclass
class _Run:
    chat_id: str
    dataset_id: str
    message_id: int
    user_message_id: int
    parts: list[str] = field(default_factory=list)
    listeners: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    user_cancelled: bool = False
    # Set once the call is over and the reply is being stored: from then on a Stop, a
    # delete or a shutdown waits for the store instead of cancelling it.
    ending: bool = False
    final: tuple[str, dict] | None = None
    # The stored tool calls so far, as `ToolCallOut` payloads, for a listener that joins
    # mid-reply.
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def start_event(self) -> tuple[str, dict]:
        return "start", {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "user_message_id": self.user_message_id,
            "content": self.text,
            "tool_calls": list(self.tool_calls),
        }

    def emit(self, event: str, data: dict) -> None:
        for q in list(self.listeners):
            q.put_nowait((event, data))


# message id -> its run, while the reply is being written.
_runs: dict[int, _Run] = {}
# Chats with a send between its refusal checks and its run being registered: `start`
# awaits several times in that window, and a double-click must not slip a second send in.
_starting: set[str] = set()


def active_for_chat(chat_id: str) -> _Run | None:
    return next((r for r in _runs.values() if r.chat_id == chat_id), None)


def get_run(message_id: int) -> _Run | None:
    return _runs.get(message_id)


def local_day() -> str:
    """The daily budget's key: the machine's local calendar date."""
    return datetime.now().strftime("%Y-%m-%d")


def scrub(text: str, secret: str) -> str:
    """An error message fit to store and show: without the key, at most 500 chars."""
    if secret and len(secret) >= 4:
        text = text.replace(secret, "***")
    return text[:500]


def estimate_tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


# --- Budgets -------------------------------------------------------------------------


async def billed_used(db: AsyncSession, *, chat_id: str | None = None) -> int:
    """Billed tokens used by one chat, or today (`local_day`) when `chat_id` is None.
    Summed from `llm_usage` rows each time; there is no counter to drift."""
    total = func.coalesce(func.sum(LlmUsage.tokens_in + LlmUsage.tokens_out), 0)
    where = LlmUsage.chat_id == chat_id if chat_id is not None else LlmUsage.day == local_day()
    return int(await db.scalar(select(total).where(where, LlmUsage.billed.is_(True))) or 0)


async def budget_status(db: AsyncSession, chat: Chat, provider: OpenAIProvider | None, estimate: int) -> BudgetOut:
    """Where this chat and today stand against the budgets, for a send of about
    `estimate` tokens. Only billed providers meet them; billed usage alone counts."""
    settings = await get_thresholds(db)
    billed = provider is not None and provider_is_billed(provider)
    chat_used = await billed_used(db, chat_id=chat.id)
    day_used = await billed_used(db)
    over = billed and (
        chat_used + estimate > settings.analyst_chat_budget_tokens
        or day_used + estimate > settings.analyst_daily_budget_tokens
    )
    return BudgetOut(
        billed=billed,
        estimate=estimate,
        chat_used=chat_used,
        chat_budget=settings.analyst_chat_budget_tokens,
        day_used=day_used,
        day_budget=settings.analyst_daily_budget_tokens,
        would_exceed=over,
    )


def send_estimate(input_tokens: int, provider: OpenAIProvider | None) -> int:
    """The tokens a send could cost at most: its context plus a full-length reply."""
    return input_tokens + (provider.max_tokens if provider is not None else 0)


# --- Starting a send -------------------------------------------------------------------


async def start(db: AsyncSession, chat: Chat, content: str) -> _Run:
    """Store the user's message and the reply's row, then start the call. Raises an
    HTTPException, having stored nothing, when the send is refused."""
    provider = await db.get(OpenAIProvider, chat.provider_id) if chat.provider_id else None
    if provider is None:
        raise HTTPException(422, "Choose a provider for this chat first.")
    model = chat.model or provider.default_model
    if not model:
        raise HTTPException(422, "Choose a model for this chat, or set the provider's default model.")
    if chat.id in _starting or active_for_chat(chat.id) is not None:
        raise HTTPException(409, "A reply is still being written in this chat. Wait for it or stop it first.")
    # No await between the check above and this line, so two sends cannot both pass it.
    _starting.add(chat.id)
    try:
        return await _start(db, chat, content, provider, model)
    finally:
        _starting.discard(chat.id)


async def _start(db: AsyncSession, chat: Chat, content: str, provider: OpenAIProvider, model: str) -> _Run:
    preview = await build_context(db, chat, draft=content)
    budget = await budget_status(db, chat, provider, send_estimate(preview.manifest.estimated_tokens, provider))
    if budget.would_exceed:
        raise HTTPException(409, (
            f"This message could use up to about {budget.estimate:,} tokens, which would pass a budget: "
            f"this chat has used {budget.chat_used:,} of {budget.chat_budget:,}, and today's chats "
            f"{budget.day_used:,} of {budget.day_budget:,}. Raise the budgets in Settings → Analyst, "
            f"or use a provider that is not billed."
        ))

    next_seq = int(await db.scalar(
        select(func.coalesce(func.max(ChatMessage.seq), -1)).where(ChatMessage.chat_id == chat.id)
    )) + 1
    user_msg = ChatMessage(chat_id=chat.id, seq=next_seq, role="user", content=content)
    db.add(user_msg)
    if not chat.title:
        line = " ".join(content.split())
        chat.title = line if len(line) <= TITLE_MAX else line[: TITLE_MAX - 1].rstrip() + "…"
    chat.updated_at = datetime.utcnow()
    await db.commit()

    built = await build_context(db, chat)
    reply = ChatMessage(
        chat_id=chat.id, seq=next_seq + 1, role="assistant", content="", status="partial", model=model,
        context_manifest=built.manifest.model_dump(),
        findings_summary=built.findings_summary, image_count=built.image_count,
    )
    db.add(reply)
    await db.commit()

    cfg = provider_snapshot(provider)
    run = _Run(chat_id=chat.id, dataset_id=chat.dataset_id, message_id=reply.id, user_message_id=user_msg.id)
    _runs[reply.id] = run
    messages = [m.model_dump() for m in built.messages]
    run.task = asyncio.create_task(_execute(run, cfg, model, messages, built.tools, built.privacy))
    return run


# --- The call --------------------------------------------------------------------------


async def _flush_loop(run: _Run) -> None:
    """Write the reply so far to its row about once a second."""
    from backend.database import AsyncSessionLocal

    written = 0
    while True:
        await asyncio.sleep(FLUSH_EVERY_S)
        text = run.text
        if len(text) == written:
            continue
        try:
            async with AsyncSessionLocal() as s:
                await s.execute(
                    update(ChatMessage).where(ChatMessage.id == run.message_id).values(content=text)
                )
                await s.commit()
            written = len(text)
        except Exception:  # noqa: BLE001 — the final write still lands
            logger.warning("Could not save the partial reply of chat message %s", run.message_id, exc_info=True)


def _request_chars(messages: list[dict], tools: list[dict]) -> int:
    """A character count of a request, for a token estimate when the server reports none
    and for the budget check between rounds."""
    # `ensure_ascii=False`: a `\uXXXX` escape would count one character as six.
    return len(json.dumps(messages, ensure_ascii=False)) + (
        len(json.dumps(tools, ensure_ascii=False)) if tools else 0)


async def _budget_stop(run: _Run, cfg: _Provider, chars: int) -> str | None:
    """Before a round after the first: why it may not run, or None. Only a billed
    provider meets the budgets, as at the send."""
    from backend.database import AsyncSessionLocal

    if not cfg.billed:
        return None
    estimate = estimate_tokens(chars) + cfg.max_tokens
    async with AsyncSessionLocal() as s:
        settings = await get_thresholds(s)
        chat_used = await billed_used(s, chat_id=run.chat_id)
        day_used = await billed_used(s)
    if (chat_used + estimate <= settings.analyst_chat_budget_tokens
            and day_used + estimate <= settings.analyst_daily_budget_tokens):
        return None
    return (
        f"The reply stopped before its next step, which could use about {estimate:,} tokens: this chat "
        f"has used {chat_used:,} of {settings.analyst_chat_budget_tokens:,}, and today's chats {day_used:,} "
        f"of {settings.analyst_daily_budget_tokens:,}. Raise the budgets in Settings → Analyst."
    )


async def _record_round(run: _Run, cfg: _Provider, tokens: tuple[int, int, bool], totals: list) -> None:
    """Commit one finished tool round's usage at once, before its tools run: a crash or
    a Stop later in the turn must not lose a call that may have been billed."""
    from backend.database import AsyncSessionLocal

    tokens_in, tokens_out, estimated = tokens
    async with AsyncSessionLocal() as s:
        s.add(LlmUsage(
            day=local_day(), chat_id=run.chat_id, message_id=run.message_id, provider_id=cfg.id,
            tokens_in=tokens_in, tokens_out=tokens_out, estimated=estimated, billed=cfg.billed,
        ))
        await s.commit()
    totals[0] += tokens_in
    totals[1] += tokens_out
    totals[2] = totals[2] or estimated
    totals[3] += 1
    run.emit("usage", {"tokens_in": totals[0], "tokens_out": totals[1], "estimated": totals[2]})


async def _run_tools(run: _Run, rnd: int, calls: list, privacy: dict[str, bool]) -> list[tuple]:
    """Run one round's calls, store each as a `ChatToolCall` row, and merge the
    categories their results carried into the reply's manifest in the same commit, so
    the history rule withholds this reply once one of them is turned off. Returns
    (call, result text) pairs, in order, for the next request."""
    from backend.database import AsyncSessionLocal

    out: list[tuple] = []
    rows: list[ChatToolCall] = []
    sent: dict[str, int] = {}
    async with AsyncSessionLocal() as s:
        ds = await s.get(Dataset, run.dataset_id)
        for i, call in enumerate(calls):
            run.emit("tool_call", {"round": rnd, "call_id": call.id, "name": call.name, "arguments": call.arguments})
            title = f"Result of {call.name}"
            if i >= MAX_CALLS_PER_ROUND:
                render = render_tool_result(title, [], privacy, error=(
                    f"Only {MAX_CALLS_PER_ROUND} tool calls run in one step; this one did not. "
                    "Call it again if you still need it."))
            elif ds is None:
                render = render_tool_result(title, [], privacy, error="The dataset no longer exists.")
            else:
                render = await analyst_tools.run_tool(s, ds, call.name, call.arguments, privacy)
            for c in render.categories:
                sent[c] = sent.get(c, 0) + render.chars
            rows.append(ChatToolCall(
                message_id=run.message_id, round=rnd, call_id=call.id[:128], name=call.name[:64],
                arguments=call.arguments[:ARGUMENTS_MAX_CHARS], status="error" if render.error else "ok",
                result_text=render.text, categories=render.categories, chars=render.chars,
            ))
            out.append((call, render.text))
        msg = await s.get(ChatMessage, run.message_id)
        if msg is not None:
            s.add_all(rows)
            # A copy: an in-place edit of a loaded JSON column is not seen as a change.
            manifest = dict(msg.context_manifest or {})
            categories = {k: dict(v) for k, v in (manifest.get("categories") or {}).items()}
            for c, chars in sent.items():
                entry = categories.setdefault(c, {"sent": False, "chars": 0, "items": 0})
                entry["sent"] = True
                entry["chars"] = int(entry.get("chars") or 0) + chars
                entry["items"] = int(entry.get("items") or 0) + 1
            manifest["categories"] = categories
            manifest["tool_calls"] = int(manifest.get("tool_calls") or 0) + len(rows)
            msg.context_manifest = manifest
            await s.commit()
            for row in rows:
                payload = ToolCallOut.model_validate(row).model_dump(mode="json")
                run.tool_calls.append(payload)
                run.emit("tool_result", payload)
    return out


async def _execute(
    run: _Run, cfg: _Provider, model: str, messages: list[dict], tools: list[dict], privacy: dict[str, bool],
) -> None:
    status, error_kind, detail = "complete", None, None
    # The current call's state, reset when a tool round's usage is recorded.
    opened = sent = refused = False
    result = None
    round_start = 0  # len(run.parts) when the current call began
    prompt_chars = _request_chars(messages, tools)
    # The tool rounds already recorded: tokens in, tokens out, any estimated, how many.
    totals: list = [0, 0, False, 0]
    rnd = 0
    tool_chars = 0
    gap = False  # the next text starts a new round's: separate it from the last

    def on_send() -> None:
        nonlocal sent
        sent = True

    def on_open() -> None:
        nonlocal opened
        opened = True

    def on_text(piece: str) -> None:
        nonlocal gap
        if gap:
            gap = False
            if run.text.strip():
                run.parts.append("\n\n")
                run.emit("delta", {"text": "\n\n"})
        run.parts.append(piece)
        run.emit("delta", {"text": piece})

    flusher = asyncio.create_task(_flush_loop(run))
    try:
        while True:
            offer = bool(tools) and rnd < MAX_TOOL_ROUNDS and tool_chars < TOOL_TURN_MAX_CHARS
            prompt_chars = _request_chars(messages, tools)
            if rnd:
                stop = await _budget_stop(run, cfg, prompt_chars)
                if stop is not None:
                    error_kind, detail = "budget", stop
                    if not run.text.strip():
                        status = "error"
                    break
            opened = sent = refused = False
            result = None
            round_start = len(run.parts)
            async with llm_limiter.slot(cfg.id, cfg.max_concurrent):
                client = openai_compat_captioner.make_client(cfg.base_url, cfg.api_key, cfg.timeout_s)
                if client is None:
                    raise ChatStreamError("fatal", "The openai package is not installed on this server.", opened=False)
                try:
                    # Past the caps the tools are still sent, as "none": some servers refuse
                    # tool messages in a request that offers no tools.
                    result = await stream_chat(
                        client, model=model, messages=messages, max_tokens=cfg.max_tokens,
                        first_token_s=FIRST_TOKEN_S, idle_s=IDLE_S, on_text=on_text, on_open=on_open,
                        on_send=on_send, tools=tools or None, tool_choice="auto" if offer else "none",
                    )
                finally:
                    await client.close()
            calls = [c for c in result.tool_calls if c.name]
            if not (offer and calls and result.finish_reason != "length"):
                break
            # A tool round: its usage first, then its tools.
            round_text = "".join(run.parts[round_start:])
            tokens = (
                result.tokens_in if result.tokens_in is not None else estimate_tokens(prompt_chars),
                result.tokens_out if result.tokens_out is not None
                else estimate_tokens(len(round_text) + sum(len(c.arguments) for c in calls)),
                result.tokens_in is None or result.tokens_out is None,
            )
            # Shielded, so a Stop cannot cut the commit short; and on a Stop, wait for it
            # and mark the round recorded when it landed, or `_finish` would record it again.
            recording = asyncio.ensure_future(_record_round(run, cfg, tokens, totals))
            try:
                await asyncio.shield(recording)
            except asyncio.CancelledError:
                await asyncio.wait({recording})
                if not recording.cancelled() and recording.exception() is None:
                    opened = sent = refused = False
                    result = None
                    round_start = len(run.parts)
                raise
            opened = sent = refused = False
            result = None
            round_start = len(run.parts)
            answers = await _run_tools(run, rnd, calls, privacy)
            messages.append({
                "role": "assistant",
                "content": round_text.strip() or None,
                "tool_calls": [c.as_message_call() for c in calls],
            })
            for call, text in answers:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
                tool_chars += len(text)
            rnd += 1
            gap = True
        if status == "complete" and error_kind is None and result is not None:
            # The last call's text: earlier rounds' "Let me look." is no answer.
            answer = "".join(run.parts[round_start:])
            if result.finish_reason == "length":
                error_kind = "length"
                detail = "The reply was cut off by the provider's Max tokens (Settings → LLM Providers)."
                if not run.text.strip():
                    status = "error"
            elif not answer.strip() and result.tool_calls:
                status, error_kind = "error", "tools"
                detail = "The model kept asking for tools after its limit and wrote no answer."
            elif not answer.strip():
                status, error_kind, detail = "error", "empty", "The model sent back an empty reply."
    except ChatStreamError as exc:
        status, error_kind, detail = "error", exc.kind, scrub(str(exc), cfg.api_key)
        opened = opened or exc.opened
        refused = exc.refused
        logger.warning("Analyst chat %s: the call failed (%s)", run.chat_id, exc.kind)
    except asyncio.CancelledError:
        if run.user_cancelled:
            status = "cancelled"
        else:
            status, error_kind, detail = "error", "interrupted", INTERRUPTED_DETAIL
    except Exception as exc:  # noqa: BLE001 — never leave a row partial
        # The message only, scrubbed: an exception's text is the one place a key could ride.
        logger.error("Analyst chat %s: unexpected failure: %s: %s", run.chat_id,
                     type(exc).__name__, scrub(str(exc), cfg.api_key))
        status, error_kind, detail = "error", "fatal", "Something went wrong in Crucible; see the log."
    finally:
        # From here on, a Stop, a delete or a shutdown waits for the store (`_stop`).
        run.ending = True
        flusher.cancel()
        await asyncio.wait({flusher})

    # The last call: recorded once the server accepted it or text arrived, or, for a billed
    # provider, once it was sent and not refused (it may have been billed). Only an HTTP
    # refusal is known to have run nothing. Tool rounds were recorded as they ended.
    last_text = "".join(run.parts[round_start:])
    record = opened or bool(last_text) or (cfg.billed and sent and not refused)
    tokens_in = result.tokens_in if result is not None else None
    tokens_out = result.tokens_out if result is not None else None
    estimated = tokens_in is None or tokens_out is None
    if record:
        tokens_in = tokens_in if tokens_in is not None else estimate_tokens(prompt_chars)
        tokens_out = tokens_out if tokens_out is not None else estimate_tokens(len(last_text))
    try:
        # Shielded: nothing that cancels this task can leave the row `partial` and the
        # listeners waiting. `_stop` no longer cancels once `ending` is set.
        await asyncio.shield(_finish(
            run, cfg, status, error_kind, detail, tokens_in, tokens_out, estimated, record, totals))
    except Exception:  # noqa: BLE001 — the store failed: still end the run for its listeners
        logger.error("Analyst chat %s: could not store the reply", run.chat_id, exc_info=True)
        await _finish_failed(run)
    finally:
        _runs.pop(run.message_id, None)


async def _finish_failed(run: _Run) -> None:
    """The reply could not be stored: mark the row an error if possible, and send every
    listener a final `error` so none waits forever."""
    from backend.database import AsyncSessionLocal

    detail = "Crucible could not save this reply; see the log."
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(
                update(ChatMessage).where(ChatMessage.id == run.message_id)
                .values(status="error", error_kind="fatal", error_detail=detail, content=run.text)
            )
            await s.commit()
    except Exception:  # noqa: BLE001 — the startup sweep catches a row left partial
        logger.error("Analyst chat %s: could not mark the reply failed", run.chat_id, exc_info=True)
    run.final = ("error", {
        "id": run.message_id, "status": "error", "error_kind": "fatal",
        "error_detail": detail, "content": run.text,
    })
    run.emit(*run.final)


async def _related(db: AsyncSession, dataset_id: str, text: str) -> list[str]:
    report = await health_service.get_health(db, dataset_id, write_back=False)
    if report is None:
        return []
    known = {f.id for f in (*report.findings, *report.dismissed)}
    out: list[str] = []
    for fid in FINDING_REF_RE.findall(text):
        if fid in known and fid not in out:
            out.append(fid)
    for f in report.findings:
        if len(out) >= RELATED_MIN:
            break
        if f.id not in out:
            out.append(f.id)
    return out[:RELATED_MAX]


async def _finish(
    run: _Run, cfg: _Provider, status: str, error_kind: str | None, detail: str | None,
    tokens_in: int | None, tokens_out: int | None, estimated: bool, record: bool,
    totals: list | None = None,
) -> None:
    """Store the last call's usage, then the reply. `totals` are the tool rounds' tokens,
    already recorded; the message carries the sum of every call in the turn."""
    from backend.database import AsyncSessionLocal

    text = run.text
    prior_in, prior_out, prior_est, rounds_before = totals or (0, 0, False, 0)
    async with AsyncSessionLocal() as s:
        msg = await s.get(ChatMessage, run.message_id)
        if record:
            s.add(LlmUsage(
                day=local_day(),
                chat_id=run.chat_id if msg is not None else None,
                message_id=run.message_id if msg is not None else None,
                provider_id=cfg.id, tokens_in=tokens_in, tokens_out=tokens_out,
                estimated=estimated, billed=cfg.billed,
            ))
            # Usage before the reply: a failure writing the reply must not lose it.
            await s.commit()
        if record or rounds_before:
            sum_in = prior_in + (tokens_in if record else 0)
            sum_out = prior_out + (tokens_out if record else 0)
            sum_est = bool(prior_est or (record and estimated))
        if record:
            run.emit("usage", {"tokens_in": sum_in, "tokens_out": sum_out, "estimated": sum_est})
        if msg is None:
            payload = {"message_id": run.message_id, "status": status}
        else:
            msg.content = text
            msg.status = status
            msg.error_kind = error_kind
            msg.error_detail = detail
            if record or rounds_before:
                msg.tokens_in, msg.tokens_out, msg.tokens_estimated = sum_in, sum_out, sum_est
            if status != "error" or text:
                try:
                    msg.related_findings = await _related(s, run.dataset_id, text)
                except Exception:  # noqa: BLE001 — the strip is a nicety
                    logger.warning("Could not pick related findings for chat %s", run.chat_id, exc_info=True)
            # Only a reply that finished: a stopped or cut-off one may hold half a block.
            if status == "complete" and error_kind is None:
                try:
                    await note_proposals.store(s, msg)
                except Exception:  # noqa: BLE001 — a reply without its proposals still stands
                    logger.warning("Could not read the note proposals of chat %s", run.chat_id, exc_info=True)
                    msg.proposals.clear()
            await s.commit()
            payload = ChatMessageOut.model_validate(msg).model_dump(mode="json")
    run.final = ("error" if status == "error" else "done", payload)
    run.emit(*run.final)


# --- Listening, cancelling, sweeping ------------------------------------------------------


def listen(run: _Run) -> asyncio.Queue:
    """A queue that gets the run's `start` (with the text so far) and every event after."""
    q: asyncio.Queue = asyncio.Queue()
    q.put_nowait(run.start_event())
    if run.final is not None:
        q.put_nowait(run.final)
    else:
        run.listeners.add(q)
    return q


def stop_listening(run: _Run, q: asyncio.Queue) -> None:
    run.listeners.discard(q)


async def _stop(run: _Run, *, by_user: bool) -> None:
    """Cancel the run's call, unless it is already storing its reply, and wait until it
    has ended. `asyncio.wait` neither passes a cancel of the caller into the task nor
    swallows one aimed at the caller (PM-027)."""
    run.user_cancelled = run.user_cancelled or (by_user and not run.ending)
    task = run.task
    if task is None or task.done():
        return
    if not run.ending:
        task.cancel()
    await asyncio.wait({task})


async def cancel(message_id: int) -> bool:
    """Stop one reply, keeping its text as `cancelled`. False when none is running."""
    run = _runs.get(message_id)
    if run is None:
        return False
    await _stop(run, by_user=True)
    return True


async def cancel_chat(chat_id: str) -> None:
    for run in [r for r in _runs.values() if r.chat_id == chat_id]:
        await _stop(run, by_user=True)


async def cancel_dataset(dataset_id: str) -> None:
    """Stop every reply in a dataset's chats before the dataset is deleted."""
    for run in [r for r in _runs.values() if r.dataset_id == dataset_id]:
        await _stop(run, by_user=True)


async def stop_all() -> None:
    """At shutdown: every reply in flight ends as `error: interrupted`."""
    for run in list(_runs.values()):
        await _stop(run, by_user=False)


async def mark_interrupted() -> int:
    """At startup: a `partial` row left by a crash becomes `error: interrupted`."""
    from backend.database import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        result = await s.execute(
            update(ChatMessage)
            .where(ChatMessage.status == "partial")
            .values(status="error", error_kind="interrupted", error_detail=INTERRUPTED_DETAIL)
        )
        await s.commit()
        return result.rowcount or 0
