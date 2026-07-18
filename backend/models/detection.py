from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.ml.mask_utils import detection_mask_area


class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    image_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    bbox: Mapped[list] = mapped_column(JSON, nullable=False)  # [x1, y1, x2, y2] normalized 0-1
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    mask: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fraction (0-1) of the image covered by this detection's geometry, kept in
    # sync by the attribute listeners below. Approximate (polygon shoelace sum, or
    # bbox area when no polygon) — Stats reads this column instead of parsing
    # polygon JSON per request. NULL only for legacy rows not yet backfilled;
    # consumers coalesce NULL → 0.
    mask_area: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


@event.listens_for(Detection.mask, "set")
def _sync_mask_area_from_mask(target, value, oldvalue, initiator):
    """Recompute mask_area whenever mask is assigned (uses incoming mask + current bbox)."""
    target.mask_area = detection_mask_area(value, getattr(target, "bbox", None))


@event.listens_for(Detection.bbox, "set")
def _sync_mask_area_from_bbox(target, value, oldvalue, initiator):
    """Recompute mask_area whenever bbox is assigned (uses current mask + incoming bbox).

    Between the two listeners whichever attribute is assigned last wins; bbox is
    non-nullable so it is always assigned on construction, guaranteeing mask_area
    is set even for bbox-only (no-mask) detections. Covers every write path in
    routers/detection.py — run branches, /manual, /merge constructors, and
    /refine's in-place ``row.mask = ... / row.bbox = ...`` mutation — with zero
    call-site changes. A raw update(Detection)/SQL write to mask or bbox bypasses
    this; geometry must always be written via ORM attribute assignment.
    """
    target.mask_area = detection_mask_area(getattr(target, "mask", None), value)
