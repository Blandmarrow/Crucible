from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.utils import count_caption_tokens


class Image(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), default="")
    subfolder: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_auto_named: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    # Dimensions
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Dedup
    phash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Quality scores
    nsfw_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    aesthetic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    blur_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    noise_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    uniformity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    watermark_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    color_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    saturation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    style_similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    clip_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    dino_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    dino_layer_embeddings: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    dino_layer_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # AI generation metadata (PNG text chunks from SD/ComfyUI/Flux)
    generation_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Log of destructive replace operations: [{op, params..., at}]
    processing_history: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Manual sort order (NULL = no custom order set; NULLS LAST when sorting)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Caption
    caption_text: Mapped[str] = mapped_column(Text, default="")
    # GPT-2 BPE token count of caption_text, kept in sync by the attribute listener below.
    # Removes caption tokenization from Stats/gallery hot paths. NULL only for legacy rows
    # not yet backfilled; consumers coalesce NULL to 0.
    caption_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caption_style: Mapped[str] = mapped_column(String(32), default="")
    captioned_by: Mapped[str] = mapped_column(String(128), default="")
    captioned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def has_dino_layer_embeddings(self) -> bool:
        return self.dino_layer_embeddings is not None

    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="images")

    __table_args__ = (
        Index("ix_images_dataset_aesthetic", "dataset_id", "aesthetic_score"),
        Index("ix_images_dataset_blur", "dataset_id", "blur_score"),
        Index("ix_images_dataset_similarity", "dataset_id", "style_similarity_score"),
        Index("ix_images_dataset_subfolder", "dataset_id", "subfolder"),
        Index("ix_images_dataset_sort_order", "dataset_id", "sort_order"),
        Index("ix_images_dataset_caption_tokens", "dataset_id", "caption_token_count"),
        UniqueConstraint("dataset_id", "filename", name="uq_dataset_filename"),
    )


@event.listens_for(Image.caption_text, "set")
def _sync_token_count(target, value, oldvalue, initiator):
    """Keep caption_token_count in sync on every ORM assignment to caption_text.

    Covers all 12+ write sites (manual edits, captioning jobs, bulk edit, find/replace,
    import/rescan, tag consolidation, version restore) and the Image(caption_text=...)
    constructor. Raw SQL / update(Image) writes to caption_text bypass this — captions
    must always be written via ORM attribute assignment (true everywhere today).
    """
    target.caption_token_count = count_caption_tokens(value)
