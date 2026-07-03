from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class DatasetBranch(Base):
    __tablename__ = "dataset_branches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    head_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("dataset_versions.id", use_alter=True, name="fk_branch_head", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    versions: Mapped[list["DatasetVersion"]] = relationship(
        "DatasetVersion",
        back_populates="branch",
        foreign_keys="DatasetVersion.branch_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("uq_branch_dataset_name", "dataset_id", "name", unique=True),
    )


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("dataset_branches.id", ondelete="SET NULL"), nullable=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    branch: Mapped["DatasetBranch | None"] = relationship(
        "DatasetBranch",
        back_populates="versions",
        foreign_keys=[branch_id],
    )
    image_states: Mapped[list["VersionImageState"]] = relationship(
        "VersionImageState",
        back_populates="version",
        cascade="all, delete-orphan",
    )


class VersionImageState(Base):
    __tablename__ = "version_image_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(String(36), ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    image_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    subfolder: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)

    caption_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quality_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Quality scores
    aesthetic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    blur_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    noise_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    uniformity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    watermark_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    color_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    style_similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    dino_layer_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generation_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    processing_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    version: Mapped["DatasetVersion"] = relationship("DatasetVersion", back_populates="image_states")
