from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class ComfyPlan(Base):
    """A named ComfyUI generation plan for a dataset.

    Holds an API-format workflow template plus the list of pinned parameters
    (workflow node inputs exposed for per-row override). Rows supply values
    for pinned params; a missing/blank value falls back to the template.
    """

    __tablename__ = "comfy_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # ComfyUI API-format workflow: {node_id: {class_type, inputs, ...}}
    workflow_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # [{node_id, input, alias, is_prompt, per_row, value, int_mode}] — per_row=False pins
    # are run defaults (no queue column); value overrides the template for all rows;
    # int_mode (integer params) = fixed | random | increment when a row has no value.
    pinned_params: Mapped[list] = mapped_column(JSON, default=list)
    # Import images from these workflow nodes' history outputs (any type, incl. temp
    # previews) instead of the default "all type=output images" behavior. [] = auto.
    output_node_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rows: Mapped[list["ComfyRow"]] = relationship(
        "ComfyRow",
        back_populates="plan",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("uq_comfy_plan_dataset_name", "dataset_id", "name", unique=True),
    )


class ComfyLibraryPrompt(Base):
    """A saved prompt in the global prompt library, grouped by free-text category.

    Deliberately global (no dataset/plan FK): prompts describe image content and
    style, so they are reusable across datasets without duplicating plans.
    """

    __tablename__ = "comfy_library_prompts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ComfyRow(Base):
    """One queued generation: per-row values for a plan's pinned params."""

    __tablename__ = "comfy_rows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("comfy_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # {alias: json-native value}; absent alias = use template value
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    # pending | running | completed | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending", default="pending")
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    # First/primary output image (referential integrity + cheap UI link)
    image_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("images.id", ondelete="SET NULL"), nullable=True)
    # All output image ids (multi-SaveImage workflows produce several per row)
    image_ids: Mapped[list] = mapped_column(JSON, default=list)
    # Last ComfyUI prompt id returned by POST /prompt (debugging/idempotency)
    prompt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    plan: Mapped["ComfyPlan"] = relationship("ComfyPlan", back_populates="rows")
