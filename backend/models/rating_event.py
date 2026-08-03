from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ImageRatingEvent(Base):
    """An append-only log of every keep/cut rating a human has ever given.

    `Image.aesthetic_rating` is overwritten in place, and `Image.updated_at` is a
    generic `onupdate` any edit bumps, so before this table nothing knew *when* a
    rating was given or that one had ever been revised. Two questions depend on
    that history and neither is answerable without it: how often you give the same
    image the same answer twice (your own ceiling — the number that says whether a
    learned head at 84% is failing or is at the limit of the labels), and whether
    an existing scorer already tracks your taste.

    **Sole writer: `routers/images.py::bulk_rating`**, in the same transaction as
    the rating itself. A rating written without its event is a permanent, silent
    hole in the history the ceiling is computed from.

    **A restore writes no events.** An event means "a human looked at these pixels
    and said this"; a rollback is not that. Restoring a snapshot taken before a
    considered re-rating would synthesise a disagreement the user never made. The
    cost is a deliberate non-invariant: after a restore `images.aesthetic_rating`
    can disagree with the last event for that image, so nothing may derive the
    *current* rating from this log.

    **A restore can also destroy events, unrecoverably.** `image_id` cascades, and
    `restore_snapshot(handle_extra_images="remove")` deletes `Image` rows — so an
    image the restore drops loses its history, and undoing the restore brings the
    image and its snapshotted rating back but not its events. That is the second
    divergence direction: a rated image with *zero* events, so nothing may assume
    `rated ⇒ has ≥1 event` either.

    **The `VersionImageState` mirroring rule does not reach this.** That rule is
    about columns on `Image`, and the structural guard in
    `backend/tests/test_video_lineage_mirrors.py` derives its universe from
    `Image.__table__.columns`. This is a separate table holding a history of human
    acts, not per-image authored state a snapshot restores — see above for why a
    restore must not replay it.

    Copy and derivative paths mint new `Image.id`s, so events do not travel while
    the rating does; carrying them would double-count one human decision.
    """

    __tablename__ = "image_rating_events"

    # Integer autoincrement, not a uuid string. A bulk write stamps one
    # `datetime.utcnow()` across the whole batch, so `created_at` ties are the norm
    # rather than the exception — the monotonic id is the only deterministic
    # ordering key for "consecutive events", and the self-agreement ceiling is
    # defined over consecutive pairs. Precedent for the integer form:
    # `VersionImageState`, `Detection`.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    image_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )

    # No ForeignKey, mirroring `VersionImageState.image_id`. The event records the
    # dataset the rating was *given in* — the only frame in which a rating is
    # calibrated (`docs/dev/rating.md` § The scale). A `datasets.id` FK with CASCADE
    # would destroy the rating history of an image that had since been moved out of
    # the deleted dataset.
    dataset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Nullable because a *clear* is an event: "I withdraw the judgement" is a thing
    # a human did and the log records it. The ceiling excludes pairs touching a
    # clear — a withdrawal is not a second opinion.
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # `len(ids)` of the write that produced this event. It separates "I looked at
    # this image and pressed 4" from "I swept 1,970 images to Cut", which is the
    # single largest bias in the ceiling. It is also the only fact here that exists
    # solely at write time and cannot be reconstructed afterwards, which is why it
    # goes in now. NULL means unknown — the migration's backfill.
    batch_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # No `updated_at`. The table is append-only, and an `onupdate` advertises a
    # mutation path that must not exist.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # (image_id, id) is the exact read shape: every consumer groups by image and
    # walks events in id order.
    __table_args__ = (Index("ix_image_rating_events_image", "image_id", "id"),)
