"""Analyst chats (M12 phase 2). See docs/dev/analyst/chat.md."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.schemas import UtcDatetime

# What the analyst's context can carry, each behind its own toggle. `images` is reserved
# for a vision phase and is never sent; a request to turn it on is refused.
PrivacyCategory = Literal["numbers", "identity", "notes", "tags", "captions", "file_names", "images"]
PRIVACY_CATEGORIES: tuple[str, ...] = ("numbers", "identity", "notes", "tags", "captions", "file_names", "images")
RESERVED_CATEGORIES = frozenset({"images"})


def check_privacy(value: dict[str, bool] | None) -> dict[str, bool] | None:
    """Validate a privacy override: known categories, booleans, nothing reserved on."""
    if value is None:
        return None
    unknown = set(value) - set(PRIVACY_CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown privacy categories: {', '.join(sorted(unknown))}")
    for cat in RESERVED_CATEGORIES:
        if value.get(cat):
            raise ValueError(f"'{cat}' cannot be sent yet")
    return value


class ChatOut(BaseModel):
    id: str
    dataset_id: str
    title: str
    provider_id: str | None
    model: str
    privacy: dict[str, bool] | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    message_count: int = 0

    model_config = {"from_attributes": True}


class ChatMessageOut(BaseModel):
    id: int
    seq: int
    role: str
    content: str
    status: str
    error_kind: str | None = None
    error_detail: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_estimated: bool = False
    context_manifest: dict | None = None
    related_findings: list | None = None
    created_at: UtcDatetime

    model_config = {"from_attributes": True}


class ChatDetail(ChatOut):
    messages: list[ChatMessageOut]


class ChatCreate(BaseModel):
    title: str = Field("", max_length=200)
    provider_id: str | None = Field(None, max_length=36)
    model: str = Field("", max_length=255)


class ChatUpdate(BaseModel):
    """Absent fields are left alone. `privacy: null` clears the override (read through
    `model_fields_set`), and a provider change clears it too."""

    title: str | None = Field(None, max_length=200)
    provider_id: str | None = Field(None, max_length=36)
    model: str | None = Field(None, max_length=255)
    privacy: dict[str, bool] | None = None

    @field_validator("privacy")
    @classmethod
    def _check_privacy(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        return check_privacy(v)


class PreviewRequest(BaseModel):
    # The message being typed; the preview then shows exactly what sending it would send.
    draft: str = Field("", max_length=20_000)


class ContextMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class CategoryManifest(BaseModel):
    sent: bool
    chars: int = 0
    items: int = 0


class ContextManifest(BaseModel):
    categories: dict[str, CategoryManifest]
    # Where each category's on/off came from: the chat's override or the provider default.
    privacy_source: Literal["chat", "provider", "none"]
    history_messages: int = 0
    history_dropped: int = 0
    total_chars: int = 0
    # A character estimate (chars / 4); the real count comes from the server's usage.
    estimated_tokens: int = 0
    # Section -> the note item ids in the order the context numbered them (1-based).
    note_numbers: dict[str, list[str]] = Field(default_factory=dict)


class PreviewOut(BaseModel):
    messages: list[ContextMessage]
    manifest: ContextManifest
    privacy: dict[str, bool]
