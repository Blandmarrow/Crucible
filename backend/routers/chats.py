"""Analyst chats (M12 phase 2): a dataset's conversations and the "what gets sent"
preview. Sending a message arrives with the streaming call. See docs/dev/analyst/chat.md.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Chat, ChatMessage, Dataset, OpenAIProvider
from backend.schemas.chat import (
    ChatCreate,
    ChatDetail,
    ChatMessageOut,
    ChatOut,
    ChatUpdate,
    PreviewOut,
    PreviewRequest,
)
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
    await db.delete(chat)
    await db.commit()


@router.post("/chats/{chat_id}/preview-context", response_model=PreviewOut)
async def preview_context(chat_id: str, body: PreviewRequest, db: AsyncSession = Depends(get_db)):
    """Exactly what the next send would use: the same `build_context` call."""
    chat = await _get_chat(db, chat_id)
    built = await build_context(db, chat, draft=body.draft)
    return PreviewOut(messages=built.messages, manifest=built.manifest, privacy=built.privacy)
