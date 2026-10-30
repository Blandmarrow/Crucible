from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class CaptionPromptPreset(Base):
    """A saved captioning prompt, shared by every browser that opens this install.

    `model` may be "": a preset saved before a model was chosen has none, and the
    surfaces that load one keep whatever model they already hold.
    """

    __tablename__ = "caption_prompt_presets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(500), nullable=False)
    style: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DeclaredCategory(Base):
    """A category name the user created before any dataset uses it.

    `Dataset.category` is a plain string, so a category with no datasets has no other
    record. Its length matches that column, since a rename moves names between them.
    """

    __tablename__ = "declared_categories"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
