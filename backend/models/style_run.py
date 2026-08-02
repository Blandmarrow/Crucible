from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

# How many reference image ids a descriptor stores. The references are a
# *description* of the run, not an authoritative record — the detail panel shows
# a thumbnail strip and says "+N more" past this — so the cap keeps one JSON
# column from growing with a 5000-image reference selection. `reference_count`
# carries the true number regardless.
REFERENCE_IDS_STORED_MAX = 64


class StyleSimilarityRun(Base):
    """What produced the current `Image.style_similarity_score` values in a dataset.

    One row per dataset, overwritten by every successful style-similarity run.
    `style_similarity_score` is a raw cosine whose *meaning* depends entirely on
    the mode that made it — on the same 118 images CLIP cosines span 0.53–0.93
    while DINOv2 spans 0.05–0.70, and a per-layer run below layer ~10 compresses
    everything into 0.90–0.99 (see `backend/scripts/style_gate_report.md`). Without
    this row a stored "0.62" is uninterpretable.

    **Why a table and not columns on `Dataset`.** `Dataset.updated_at` is half the
    `get_dataset_stats` cache validator (`dataset_service.get_dataset_stats`), so
    writing a descriptor onto `datasets` would evict the whole Stats aggregation on
    every style run. `DatasetOut` is also hand-built field by field in
    `list_datasets` — the trap `CLAUDE.md` names for `video_count` — so each new
    column is one more thing an omission silently zeroes.

    **The `VersionImageState` mirroring rule does not reach this.** That rule is
    about columns on `Image`; the structural guard in
    `backend/tests/test_video_lineage_mirrors.py` derives its universe from
    `Image.__table__.columns`. A run descriptor is dataset-level state about how a
    column was last computed, not per-image authored data, and a snapshot restore
    neither reads nor writes it.
    """

    __tablename__ = "style_similarity_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # Unique: the descriptor describes the values currently in the column, and
    # there is only ever one set of those. A run overwrites rather than appends.
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )

    # "clip" | "dino" | "dino_all_layers" | "combined" | "combined_all_layers".
    # Free String rather than an Enum: the POST body's `embedding_type` is a plain
    # `str` validated by the branch chain, and a mode added there must not need a
    # migration here to be describable.
    embedding_type: Mapped[str] = mapped_column(String(32), nullable=False)
    dino_layer: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Up to REFERENCE_IDS_STORED_MAX ids, deliberately *not* kept in sync with
    # `images`: a reference deleted after the run is still the truth about what the
    # run used. The detail panel hides a tile whose thumbnail 404s.
    reference_image_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reference_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Uploaded reference files (CLIP-only); they have no ids to store.
    external_reference_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NULL means the run covered the whole dataset. A number means it was scoped to
    # that many images (the SelectionToolbar path), so the rest of the dataset still
    # carries scores from some earlier run this row no longer describes. Consumers
    # test `scoped_image_count is not None` rather than a stored boolean, which
    # would go stale the moment an image is deleted.
    scoped_image_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
