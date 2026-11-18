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

Live events go to whoever is listening, through one queue per listener: `start` (with
the text so far, so a reconnect resumes), `delta`, `usage`, then `done` or `error`. The
API key is loaded from the provider row at send time and never enters a log line, an
event or a stored row; an error message is scrubbed of it. See docs/dev/analyst/sending.md.
"""
import asyncio
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
from backend.models import Chat, ChatMessage, LlmUsage, OpenAIProvider
from backend.schemas.chat import BudgetOut, ChatMessageOut
from backend.schemas.openai_provider import provider_is_billed
from backend.services import health_service, llm_limiter
from backend.services.analyst_context import CHARS_PER_TOKEN, build_context
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

# A fixed pattern, so stdlib `re` is safe (CLAUDE.md § client-supplied regex).
FINDING_REF_RE = re.compile(r"\[\[finding:([a-z0-9_]+)\]\]")

INTERRUPTED_DETAIL = "Crucible stopped before the reply finished."
TERMINAL_EVENTS = ("done", "error")


@dataclass
class _Provider:
    """What the call needs from the provider row, read once at send time."""
    id: str
    base_url: str
    api_key: str
    timeout_s: float
    max_tokens: int
    max_concurrent: int
    billed: bool


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
    final: tuple[str, dict] | None = None

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def start_event(self) -> tuple[str, dict]:
        return "start", {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "user_message_id": self.user_message_id,
            "content": self.text,
        }

    def emit(self, event: str, data: dict) -> None:
        for q in list(self.listeners):
            q.put_nowait((event, data))


# message id -> its run, while the reply is being written.
_runs: dict[int, _Run] = {}


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


async def budget_status(db: AsyncSession, chat: Chat, provider: OpenAIProvider | None, estimate: int) -> BudgetOut:
    """Where this chat and today stand against the budgets, for a send of about
    `estimate` tokens. Only billed providers meet them; billed usage alone counts."""
    settings = await get_thresholds(db)
    billed = provider is not None and provider_is_billed(provider)
    total = func.coalesce(func.sum(LlmUsage.tokens_in + LlmUsage.tokens_out), 0)
    chat_used = int(await db.scalar(select(total).where(LlmUsage.chat_id == chat.id, LlmUsage.billed.is_(True))) or 0)
    day_used = int(await db.scalar(select(total).where(LlmUsage.day == local_day(), LlmUsage.billed.is_(True))) or 0)
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
    if active_for_chat(chat.id) is not None:
        raise HTTPException(409, "A reply is still being written in this chat. Wait for it or stop it first.")

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

    cfg = _Provider(
        id=provider.id, base_url=provider.base_url, api_key=provider.api_key or "",
        timeout_s=float(provider.timeout_s), max_tokens=int(provider.max_tokens),
        max_concurrent=int(provider.max_concurrent), billed=provider_is_billed(provider),
    )
    run = _Run(chat_id=chat.id, dataset_id=chat.dataset_id, message_id=reply.id, user_message_id=user_msg.id)
    _runs[reply.id] = run
    messages = [m.model_dump() for m in built.messages]
    run.task = asyncio.create_task(_execute(run, cfg, model, messages, built.manifest.total_chars))
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


async def _execute(run: _Run, cfg: _Provider, model: str, messages: list[dict], prompt_chars: int) -> None:
    opened = False
    status, error_kind, detail = "complete", None, None
    tokens_in = tokens_out = None

    def on_open() -> None:
        nonlocal opened
        opened = True

    def on_text(piece: str) -> None:
        run.parts.append(piece)
        run.emit("delta", {"text": piece})

    flusher = asyncio.create_task(_flush_loop(run))
    try:
        async with llm_limiter.slot(cfg.id, cfg.max_concurrent):
            client = openai_compat_captioner.make_client(cfg.base_url, cfg.api_key, cfg.timeout_s)
            if client is None:
                raise ChatStreamError("fatal", "The openai package is not installed on this server.", opened=False)
            try:
                result = await stream_chat(
                    client, model=model, messages=messages, max_tokens=cfg.max_tokens,
                    first_token_s=FIRST_TOKEN_S, idle_s=IDLE_S, on_text=on_text, on_open=on_open,
                )
            finally:
                await client.close()
        tokens_in, tokens_out = result.tokens_in, result.tokens_out
        if result.finish_reason == "length":
            error_kind = "length"
            detail = "The reply was cut off by the provider's Max tokens (Settings → LLM Providers)."
            if not run.text.strip():
                status = "error"
        elif not run.text.strip():
            status, error_kind, detail = "error", "empty", "The model sent back an empty reply."
    except ChatStreamError as exc:
        status, error_kind, detail = "error", exc.kind, scrub(str(exc), cfg.api_key)
        opened = opened or exc.opened
        logger.warning("Analyst chat %s: the call failed (%s)", run.chat_id, exc.kind)
    except asyncio.CancelledError:
        if run.user_cancelled:
            status = "cancelled"
        else:
            status, error_kind, detail = "error", "interrupted", INTERRUPTED_DETAIL
    except Exception:  # noqa: BLE001 — never leave a row partial
        logger.exception("Analyst chat %s: unexpected failure", run.chat_id)
        status, error_kind, detail = "error", "fatal", "Something went wrong in Crucible; see the log."
    finally:
        flusher.cancel()
        try:
            await flusher
        except BaseException:  # noqa: BLE001 — its own cancel
            pass

    estimated = tokens_in is None or tokens_out is None
    record = opened or bool(run.parts)
    if record:
        tokens_in = tokens_in if tokens_in is not None else estimate_tokens(prompt_chars)
        tokens_out = tokens_out if tokens_out is not None else estimate_tokens(len(run.text))
    try:
        await _finish(run, cfg, status, error_kind, detail, tokens_in, tokens_out, estimated, record)
    finally:
        _runs.pop(run.message_id, None)


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
) -> None:
    from backend.database import AsyncSessionLocal

    text = run.text
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
        if record:
            run.emit("usage", {"tokens_in": tokens_in, "tokens_out": tokens_out, "estimated": estimated})
        if msg is None:
            payload = {"message_id": run.message_id, "status": status}
        else:
            msg.content = text
            msg.status = status
            msg.error_kind = error_kind
            msg.error_detail = detail
            if record:
                msg.tokens_in, msg.tokens_out, msg.tokens_estimated = tokens_in, tokens_out, estimated
            if status != "error" or text:
                try:
                    msg.related_findings = await _related(s, run.dataset_id, text)
                except Exception:  # noqa: BLE001 — the strip is a nicety
                    logger.warning("Could not pick related findings for chat %s", run.chat_id, exc_info=True)
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
    run.user_cancelled = run.user_cancelled or by_user
    task = run.task
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


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
