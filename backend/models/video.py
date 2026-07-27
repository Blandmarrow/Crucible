from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Video(Base):
    """A source video living flat in `{dataset.folder_path}/videos/`.

    Videos are deliberately *not* rows in `images`: that table carries ~20
    image-specific columns, FK cascades to detections, and the load-bearing
    "thumbnails are .webp keyed by stem" invariant, none of which apply to a
    video file. Extraction (Phase 2) converts a video into ordinary `Image`
    rows at the boundary, so everything downstream — dedup, scoring,
    captioning, export, versioning — stays media-unaware.

    Poster thumbnails live in `{dataset}/videos/thumbnails/`, a separate
    directory from the images thumbnail folder. See docs/dev/video.md for why a
    distinguishing suffix inside the shared folder would have been a trap.
    """

    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)

    # No `subfolder` column: videos are flat in videos/. Frames get the
    # subfolder-per-video treatment instead (Phase 2).
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), default="")
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    poster_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # NULL means "could not be determined from the header" and renders as
    # "unknown" — never 0. See video_service.probe_video for the containers that
    # report a poisoned frame count.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Raw 4-character FOURCC from CAP_PROP_FOURCC; media_types.codec_label maps
    # it for display and falls back to the code itself.
    codec: Mapped[str | None] = mapped_column(String(16), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Source & license provenance, same contract as Image: NULL/"" means
    # "inherit the dataset default", resolved at read time by
    # licenses.resolve_provenance. No source_meta — nothing captures scraper
    # sidecars for video, and adding a deferred JSON column would import the
    # MissingGreenlet trap for no benefit.
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Decode fixups confirmed in the extraction modal's probe step (Phase 2) and
    # replayed verbatim by the full-res second pass (Phase 4), so pass-2 frames
    # match pass-1 geometry exactly. Four plain columns rather than a JSON rect:
    # a JSON column would need the copy-before-mutate dance for no gain.
    # All four NULL means no crop.
    crop_x: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crop_y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crop_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crop_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "" = off. Phase 2 ships "bwdif" only; telecine is detect-and-warn.
    deinterlace: Mapped[str] = mapped_column(String(16), nullable=False, default="", server_default="")
    trim_start_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    trim_end_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="videos")  # noqa: F821 — SQLAlchemy resolves the string forward ref via its registry

    __table_args__ = (
        UniqueConstraint("dataset_id", "filename", name="uq_dataset_video_filename"),
    )
