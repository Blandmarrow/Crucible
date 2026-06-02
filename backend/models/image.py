from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Image(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id"), nullable=False, index=True)
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

    # Caption / tags
    caption_text: Mapped[str] = mapped_column(Text, default="")
    caption_style: Mapped[str] = mapped_column(String(32), default="")
    captioned_by: Mapped[str] = mapped_column(String(128), default="")
    captioned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    @property
    def has_dino_layer_embeddings(self) -> bool:
        return self.dino_layer_embeddings is not None

    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="images")
    tags: Mapped[list["Tag"]] = relationship("Tag", back_populates="image", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_images_dataset_aesthetic", "dataset_id", "aesthetic_score"),
        Index("ix_images_dataset_blur", "dataset_id", "blur_score"),
        Index("ix_images_dataset_similarity", "dataset_id", "style_similarity_score"),
        Index("ix_images_dataset_subfolder", "dataset_id", "subfolder"),
        Index("ix_images_dataset_sort_order", "dataset_id", "sort_order"),
        UniqueConstraint("dataset_id", "filename", name="uq_dataset_filename"),
    )
