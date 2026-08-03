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
    # Indexed for the equality lookup the file browser does per file: every
    # move, rename and delete under `routers/filesystem.py` asks "is there a row
    # at this exact path?" once per affected file, and without an index each of
    # those is a full scan of `images`. The videos twin is `ix_videos_file_path`.
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
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
    # Which model produced `aesthetic_score`: "laion" (CLIP ViT-L/14 + LAION's
    # sac+logos+ava1 MLP) or "v2_5" (SigLIP-so400m + Aesthetic Predictor V2.5).
    # The two produce non-comparable distributions, and two consumers act
    # destructively on the score — `aesthetic_min` omits images at export and
    # `rankForKeepBest` *deletes* them — so the marker is a safety device, not
    # bookkeeping.
    #
    # NULL is load-bearing: the invariant is `aesthetic_score IS NOT NULL` ⟺
    # `aesthetic_model IS NOT NULL`, established by migration a5e1b7c3d9f0's
    # backfill and maintained by the single write site in `routers/quality.py`.
    # Hence no `default=""` (an empty string recreates exactly the ambiguity the
    # backfill removed), and no Enum or CHECK — a future learned head writes
    # `head:{uuid}` here.
    #
    # `info["qualifies"]` enrols it in the rebuild-path guards of
    # `backend/tests/test_video_lineage_mirrors.py`, which are otherwise derived
    # by the `*_score` suffix and so cannot see a column named like this one.
    aesthetic_model: Mapped[str | None] = mapped_column(
        String(64), nullable=True, info={"qualifies": "aesthetic_score"}
    )
    blur_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    noise_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    uniformity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    watermark_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    color_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    saturation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    luminance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    style_similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    clip_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    dino_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    dino_layer_embeddings: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    dino_layer_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # AI generation metadata (PNG text chunks from SD/ComfyUI/Flux)
    generation_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Source & license provenance. All nullable: NULL/"" means "inherit the
    # dataset default" (resolved at read time by licenses.resolve_provenance).
    # Materialize concrete values on cross-dataset copy/move, or the image
    # silently re-inherits the destination dataset's unrelated default.
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Long tail from scraper sidecars: scrape date, post id, uploader, raw payload.
    # Deferred like the embedding blobs — it can be several KB per row and only the
    # single-image endpoints and snapshot creation read it. Every reader must
    # `undefer` it: a lazy load on an async session raises MissingGreenlet.
    source_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, deferred=True)

    # Frame lineage: set only on images produced by video frame extraction.
    # Plain indexed columns rather than keys inside `source_meta`, which is
    # deferred=True (the MissingGreenlet trap) and unindexable — the
    # "frames from video X" filter and group-by-video both need real queries.
    # FK is SET NULL: deleting a source video must never destroy curated frames,
    # so a frame outlives its video with the timestamp and shot index intact.
    #
    # Derivatives do NOT inherit lineage: crop/upscale/LUT/detection-crop copy
    # only the five provenance keys via `licenses.copy_provenance`, so a *new*
    # derived image has all three NULL. The hazard is the **replace** mode of
    # those same operations, which mutates the row in place and therefore keeps
    # its lineage while the pixels are no longer the extracted frame. Any
    # re-extraction pass must skip or warn on a frame with a non-empty
    # `processing_history`.
    source_video_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("videos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_timestamp_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_shot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Log of destructive replace operations: [{op, params..., at}]
    processing_history: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Set by every path that rewrites this image's pixels in place (resize,
    # crop, LUT, upscale, detection crop, frame re-extraction) — the scores above
    # were measured against pixels that no longer exist. `blur_score` is Laplacian
    # variance against a fixed threshold, so it is resolution-dependent and a
    # score from a 1024px triage frame is systematically wrong for the 4K frame
    # that replaced it. Cleared only by a scoring run that refreshes every score
    # the row actually carries (`routers/quality.py`).
    #
    # Deliberately NOT named `*_score`: `SCORE_COLUMNS` in
    # `backend/tests/test_video_lineage_mirrors.py` is suffix-derived and would
    # enrol a boolean in the float-seeding guards.
    scores_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    # The human keep/cut decision: 4 = Keep, 3 = Probably, 2 = Probably not,
    # 1 = Cut, NULL = not yet rated. Higher is better, matching every other
    # numeric column here (and every photo tool's star rating), so "Rating ↓" in
    # `SORT_OPTIONS` reads best-first alongside "Aesthetic ↓".
    #
    # Authored data, not a measurement: nothing computes it and nothing
    # recomputes it, so it travels on every cross-dataset copy and move, is
    # mirrored and diffed for versioning, and is deliberately NOT inherited by a
    # derivative (crop, upscale, LUT, crop-to-detection) — a new picture has not
    # been judged.
    #
    # Deliberately NOT named `*_score`, and carrying no
    # `info={"qualifies": ...}`. `score_columns()` in `backend/utils.py` is
    # suffix-derived and pinned to exactly ten names by
    # `test_scores_stale.py::test_the_score_universe_is_the_ten_suffixed_columns`,
    # and `qualifies` means "this column says how to read a score" — a rating
    # qualifies nothing. `info={"carried": True}` is the honest enrolment into
    # the rebuild-path guards of `backend/tests/test_video_lineage_mirrors.py`.
    aesthetic_rating: Mapped[int | None] = mapped_column(
        Integer, nullable=True, info={"carried": True}
    )
    # The `scores_stale` twin for the rating: the pixels were rewritten in place
    # after a human judged them. A separate bit rather than a reuse, because the
    # **clear predicates diverge** — a quality run clears `scores_stale`, while
    # only a human re-rating clears this one. `utils.record_in_place` stays the
    # single writer of both.
    rating_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", info={"carried": True}
    )

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

    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="images")  # noqa: F821 — SQLAlchemy resolves the string forward ref via its registry

    __table_args__ = (
        Index("ix_images_dataset_aesthetic", "dataset_id", "aesthetic_score"),
        Index("ix_images_dataset_blur", "dataset_id", "blur_score"),
        Index("ix_images_dataset_similarity", "dataset_id", "style_similarity_score"),
        Index("ix_images_dataset_rating", "dataset_id", "aesthetic_rating"),
        Index("ix_images_dataset_subfolder", "dataset_id", "subfolder"),
        Index("ix_images_dataset_sort_order", "dataset_id", "sort_order"),
        Index("ix_images_dataset_caption_tokens", "dataset_id", "caption_token_count"),
        Index("ix_images_dataset_created_at", "dataset_id", "created_at"),
        Index("ix_images_dataset_caption", "dataset_id", "caption_text"),
        # Frame lineage in video order. Covers both shapes the gallery asks for:
        # filter-then-order ("frames from video X", sorted by timeline — the
        # dominant use) and the unfiltered whole-dataset lineage sort, whose
        # nulls-last ASC scan walks this index instead of sorting the table.
        # `ix_images_source_video_id` is now a redundant prefix of this; dropping
        # it would touch the delete-video NULLing UPDATE and `frames-summary`, so
        # it stays for now.
        Index("ix_images_source_video_timeline", "source_video_id", "source_timestamp_ms"),
        # No index on (dataset_id, license): every license filter runs on the
        # *effective* license, COALESCE(images.license, datasets.license), which is
        # not sargable against one. See migration b5e8d2a7c9f4.
        UniqueConstraint("dataset_id", "filename", name="uq_dataset_filename"),
    )


@event.listens_for(Image.caption_text, "set")
def _sync_token_count(target, value, oldvalue, initiator):
    """Keep caption_token_count in sync on every ORM assignment to caption_text.

    Covers all 12+ write sites (manual edits, captioning jobs, bulk edit, find/replace,
    import/rescan, tag consolidation, version restore) and the Image(caption_text=...)
    constructor. Raw SQL / update(Image) writes to caption_text bypass this and leave
    caption_token_count stale, silently — so captions must always be written via ORM
    attribute assignment. No such bulk-update write exists today; keep it that way.
    """
    target.caption_token_count = count_caption_tokens(value)
