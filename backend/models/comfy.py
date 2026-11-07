from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
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
    # Does this plan's output count as self-created? True (the default) stamps the
    # imported images `license=synthetic`, `source_name=ComfyUI`; False lets them
    # inherit the dataset's provenance defaults, which is the honest record when the
    # plan derives from licensed source material. See routers/comfy._comfy_output_provenance.
    output_is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )
    # Folders the user made on the plan's Folders tab, so an empty one survives (rows
    # only imply the folders they sit in). Reassign, never mutate in place.
    declared_folders: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    # The "Generate prompts" form (schemas/app_data.ComfyGenSettings). NULL means never
    # saved, which the browser import relies on; none_as_null keeps None from being
    # stored as JSON `null`.
    gen_settings: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rows: Mapped[list["ComfyRow"]] = relationship(
        "ComfyRow",
        back_populates="plan",
        cascade="all, delete-orphan",
    )
    blueprint: Mapped["ComfyBlueprint | None"] = relationship(
        "ComfyBlueprint",
        back_populates="plan",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        Index("uq_comfy_plan_dataset_name", "dataset_id", "name", unique=True),
    )


class ComfyBlueprint(Base):
    """A plan's structured-generation spec: axes, variation pieces and scenarios.

    One per plan. Expansion (services/blueprint_expand.py) turns it into
    `comfy_leaves`, and Apply mints rows for each leaf's shortfall. See
    docs/dev/comfyui/structured.md.
    """

    __tablename__ = "comfy_blueprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("comfy_plans.id", ondelete="CASCADE"), nullable=False)
    # schemas/comfy_blueprint.BlueprintSpec, stored as dumped JSON.
    spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Seeds every sampled combination and template walk, so re-expansion is stable.
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The outline call's last brief, and the draft spec it returned awaiting review.
    brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan: Mapped["ComfyPlan"] = relationship("ComfyPlan", back_populates="blueprint")
    leaves: Mapped[list["ComfyLeaf"]] = relationship(
        "ComfyLeaf",
        back_populates="blueprint",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("uq_comfy_blueprints_plan_id", "plan_id", unique=True),
    )


class ComfyLeaf(Base):
    """One folder a blueprint expands to: its rendered path, image target and entities.

    A leaf *is* its folder — identity is (blueprint_id, path), so renaming a label
    makes a new leaf and leaves the old one's rows naming a deleted leaf.
    """

    __tablename__ = "comfy_leaves"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    blueprint_id: Mapped[str] = mapped_column(String(36), ForeignKey("comfy_blueprints.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario: Mapped[str] = mapped_column(String(100), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    # Absolute: the images this folder should hold, never "N more".
    target: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # {axis: [entity ids]} — which entities this leaf combines.
    entity_ids: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # The next index of the leaf's template walk. A cursor, not a count of anything:
    # shortfall is always recomputed from rows and images.
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    blueprint: Mapped["ComfyBlueprint"] = relationship("ComfyBlueprint", back_populates="leaves")

    __table_args__ = (
        Index("uq_comfy_leaves_blueprint_path", "blueprint_id", "path", unique=True),
    )


class ComfyLibraryPrompt(Base):
    """A saved prompt in the global prompt library, grouped by free-text category.

    Deliberately global (no dataset/plan FK): prompts describe image content and
    style, so they are reusable across datasets without duplicating plans.
    """

    __tablename__ = "comfy_library_prompts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    category: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ComfyRow(Base):
    """One queued generation: per-row values for a plan's pinned params."""

    __tablename__ = "comfy_rows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("comfy_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Virtual gallery folder this row's outputs are filed under, nested beneath the
    # run's base folder. Same width as `Image.subfolder`. "" = the run's base folder.
    subfolder: Mapped[str] = mapped_column(String(512), nullable=False, server_default="", default="")
    # The blueprint leaf that minted this row. Deliberately **no foreign key**: a row
    # whose leaf a blueprint edit deleted should keep naming it as provenance, and an
    # inline column-level reference reads back from SQLite without its `ondelete`, which
    # `scripts/check_migrations.py` then reports as permanent drift (see the docstring
    # of `a7c3e5b1d9f2_add_image_frame_lineage.py`). Written by blueprint Apply
    # (services/blueprint_service.py); never settable through the row endpoints.
    leaf_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Where the prompt text came from: "template" (blueprint Apply), "rewritten" (the
    # §3 LLM pass), "manual" (a hand edit of the prompt through the row endpoints),
    # NULL (flat generation, bulk add). The rewrite pass touches only "template" rows,
    # which is what keeps it off a prompt the user corrected (services/blueprint_rewrite.py).
    prompt_origin: Mapped[str | None] = mapped_column(String(16), nullable=True)
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
