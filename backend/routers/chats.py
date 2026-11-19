"""Analyst chats (M12 phase 2): a dataset's conversations, the "what gets sent" preview,
and sending a message with its streamed reply. See docs/dev/analyst/chat.md and
docs/dev/analyst/sending.md.
"""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from backend.database import get_db
from backend.models import Chat, ChatMessage, ChatProposal, Dataset, OpenAIProvider
from backend.schemas.chat import (
    ChatCreate,
    ChatDetail,
    ChatMessageOut,
    ChatOut,
    ChatUpdate,
    PreviewOut,
    PreviewRequest,
    ProposalOut,
    SendRequest,
)
from backend.services import chat_runner, note_proposals
from backend.services.analyst_context import build_context

router = APIRouter(tags=["chats"])


async def _get_chat(db: AsyncSession, chat_id: str) -> Chat:
    chat = await db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(404, "Chat not found")
    return chat


async def _check_provider(db: AsyncSession, provider_id: str | None) -> None:
    if provider_id is not None and await db.get(OpenAIProvider, provider_id) is None:
        raise HTTPException(404, "Provider not found")


async def _message_counts(db: AsyncSession, chat_ids: list[str]) -> dict[str, int]:
    if not chat_ids:
        return {}
    rows = await db.execute(
        select(ChatMessage.chat_id, func.count())
        .where(ChatMessage.chat_id.in_(chat_ids))
        .group_by(ChatMessage.chat_id)
    )
    return dict(rows.all())


def _out(chat: Chat, count: int) -> ChatOut:
    out = ChatOut.model_validate(chat)
    out.message_count = count
    return out


@router.get("/datasets/{dataset_id}/chats", response_model=list[ChatOut])
async def list_chats(dataset_id: str, db: AsyncSession = Depends(get_db)):
    if await db.get(Dataset, dataset_id) is None:
        raise HTTPException(404, "Dataset not found")
    chats = list((await db.execute(
        select(Chat).where(Chat.dataset_id == dataset_id).order_by(Chat.updated_at.desc(), Chat.id)
    )).scalars().all())
    counts = await _message_counts(db, [c.id for c in chats])
    return [_out(c, counts.get(c.id, 0)) for c in chats]


@router.post("/datasets/{dataset_id}/chats", response_model=ChatOut, status_code=201)
async def create_chat(dataset_id: str, body: ChatCreate, db: AsyncSession = Depends(get_db)):
    if await db.get(Dataset, dataset_id) is None:
        raise HTTPException(404, "Dataset not found")
    await _check_provider(db, body.provider_id)
    chat = Chat(dataset_id=dataset_id, title=body.title.strip(), provider_id=body.provider_id, model=body.model)
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    return _out(chat, 0)


@router.get("/chats/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: str, db: AsyncSession = Depends(get_db)):
    chat = await _get_chat(db, chat_id)
    messages = list((await db.execute(
        select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.seq)
    )).scalars().all())
    base = _out(chat, len(messages))
    return ChatDetail(**base.model_dump(), messages=[ChatMessageOut.model_validate(m) for m in messages])


@router.patch("/chats/{chat_id}", response_model=ChatOut)
async def update_chat(chat_id: str, body: ChatUpdate, db: AsyncSession = Depends(get_db)):
    """Rename, switch provider or model, or set the chat's privacy override. A provider
    change clears the override: toggles chosen for one server must not follow the chat
    to another (a local server's "everything" to a billed one)."""
    chat = await _get_chat(db, chat_id)
    fields = body.model_fields_set
    provider_change = "provider_id" in fields and body.provider_id != chat.provider_id
    if provider_change and "privacy" in fields:
        raise HTTPException(
            422, "Change the provider first, then the privacy settings: a provider change resets them"
        )
    if provider_change:
        await _check_provider(db, body.provider_id)
    if "title" in fields and body.title is not None:
        chat.title = body.title.strip()
    if "model" in fields and body.model is not None:
        chat.model = body.model
    if provider_change:
        chat.provider_id = body.provider_id
        chat.privacy = None
    if "privacy" in fields:
        chat.privacy = body.privacy or None
    await db.commit()
    await db.refresh(chat)
    return _out(chat, (await _message_counts(db, [chat.id])).get(chat.id, 0))


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, db: AsyncSession = Depends(get_db)):
    chat = await _get_chat(db, chat_id)
    # A reply still streaming would otherwise write to rows the delete just removed.
    await chat_runner.cancel_chat(chat_id)
    await db.delete(chat)
    await db.commit()


@router.post("/chats/{chat_id}/preview-context", response_model=PreviewOut)
async def preview_context(chat_id: str, body: PreviewRequest, db: AsyncSession = Depends(get_db)):
    """Exactly what the next send would use (the same `build_context` call), and where
    the budgets stand for it."""
    chat = await _get_chat(db, chat_id)
    built = await build_context(db, chat, draft=body.draft)
    provider = await db.get(OpenAIProvider, chat.provider_id) if chat.provider_id else None
    estimate = chat_runner.send_estimate(built.manifest.estimated_tokens, provider)
    budget = await chat_runner.budget_status(db, chat, provider, estimate)
    return PreviewOut(messages=built.messages, manifest=built.manifest, privacy=built.privacy, budget=budget)


def _event_stream(run: "chat_runner._Run") -> EventSourceResponse:
    """The run's events as SSE. Leaving (a closed tab) only stops listening: the reply
    keeps being written by its task. The listener is added inside the generator, so a
    client that leaves before the first event never leaves one behind; its `start`
    carries the text so far, so nothing streamed before then is lost."""

    async def generate():
        queue = chat_runner.listen(run)
        try:
            while True:
                event, data = await queue.get()
                yield {"event": event, "data": json.dumps(data)}
                if event in chat_runner.TERMINAL_EVENTS:
                    break
        finally:
            chat_runner.stop_listening(run, queue)

    return EventSourceResponse(generate())


@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: SendRequest, db: AsyncSession = Depends(get_db)):
    """Send a message and stream the reply: `start`, `delta`…, `usage`, then `done` or
    `error` carrying the stored reply. Refusals (no provider or model, a reply already
    running, a budget) are plain HTTP errors and store nothing."""
    chat = await _get_chat(db, chat_id)
    run = await chat_runner.start(db, chat, body.content)
    return _event_stream(run)


async def _get_message(db: AsyncSession, chat_id: str, message_id: int) -> ChatMessage:
    msg = await db.get(ChatMessage, message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(404, "Message not found")
    return msg


@router.get("/chats/{chat_id}/messages/{message_id}/stream")
async def resume_stream(chat_id: str, message_id: int, db: AsyncSession = Depends(get_db)):
    """Pick a reply back up after a reload: the text so far, then the rest live. A reply
    that already ended answers with its `start` and its final event at once."""
    await _get_message(db, chat_id, message_id)
    run = chat_runner.get_run(message_id)
    if run is not None:
        return _event_stream(run)
    # The run may have ended during the read above: read the row again, as it is now.
    db.expire_all()
    msg = await _get_message(db, chat_id, message_id)
    final = ChatMessageOut.model_validate(msg).model_dump(mode="json")
    start = {"chat_id": chat_id, "message_id": msg.id, "user_message_id": None, "content": msg.content}

    async def generate():
        yield {"event": "start", "data": json.dumps(start)}
        yield {"event": "error" if msg.status == "error" else "done", "data": json.dumps(final)}

    return EventSourceResponse(generate())


async def _get_proposal(db: AsyncSession, chat: Chat, proposal_id: int) -> ChatProposal:
    p = await db.get(ChatProposal, proposal_id)
    msg = await db.get(ChatMessage, p.message_id) if p is not None else None
    if p is None or msg is None or msg.chat_id != chat.id:
        raise HTTPException(404, "Suggestion not found")
    return p


@router.post("/chats/{chat_id}/proposals/{proposal_id}/{action}", response_model=ProposalOut)
async def act_on_proposal(
    chat_id: str, proposal_id: int, action: Literal["accept", "reject", "undo"],
    db: AsyncSession = Depends(get_db),
):
    """Accept a note proposal (apply it to the notes), reject it, or undo either. See
    docs/dev/analyst/proposals.md."""
    chat = await _get_chat(db, chat_id)
    p = await _get_proposal(db, chat, proposal_id)
    if action == "accept":
        p = await note_proposals.accept(db, chat, p)
    elif action == "reject":
        p = await note_proposals.reject(db, p)
    else:
        p = await note_proposals.undo(db, chat, p)
    return ProposalOut.model_validate(p)


@router.post("/chats/{chat_id}/messages/{message_id}/cancel", response_model=ChatMessageOut)
async def cancel_message(chat_id: str, message_id: int, db: AsyncSession = Depends(get_db)):
    """Stop a reply, keeping what arrived as `cancelled`. Cancelling one that already
    ended changes nothing and returns it as it is."""
    await _get_message(db, chat_id, message_id)
    await chat_runner.cancel(message_id)
    db.expire_all()
    return ChatMessageOut.model_validate(await _get_message(db, chat_id, message_id))
