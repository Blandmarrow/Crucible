from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Run(Base):
    """An automated dataset run (M6 §4): a list of stages stepped forward on job completion.

    The run is not a job. Each stage that does work queues an ordinary child job
    tagged with `config["run_id"]`; when that job reaches a terminal state the
    worker's post-terminal hook calls `run_service.advance_run`, which records the
    stage and queues the next one. Nothing waits. See docs/dev/comfyui/automation.md.
    """

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: a finished run stays readable as history after its plan
    # is deleted. A live run whose plan goes fails at its next ComfyUI stage.
    plan_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("comfy_plans.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # schemas/run.RunRecipe, dumped: which stages run and each one's settings.
    recipe: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # One entry per stage, in order: {key, status, job_id, note, summary,
    # started_at, finished_at}. Status: pending | running | done | skipped |
    # failed | cancelled. Replace the list to change it, never mutate it in place.
    stages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # {"row_ids": [...]}: the plan rows this run generates. Row ids rather than image
    # ids — the images are a query over the rows' `image_ids`, and a JSON column of
    # thousands of uuids would ride on every poll of the run.
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # running | paused | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    pause_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # No FK: finished job rows are swept after 30 days, and the run outlives them.
    current_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
