from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class OpenAIProvider(Base):
    __tablename__ = "openai_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    default_model: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    max_image_px: Mapped[int] = mapped_column(Integer, nullable=False, default=1024, server_default="1024")
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=2048, server_default="2048")
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=300, server_default="300")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
