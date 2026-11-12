"""The shared job loop for upscale and LUT (M8 stage 3).

The two jobs were about 95% the same code. What differs is held in an
``EditSpec``: the job type and the words in its messages, the output suffix,
the pixel function, and the ``record_in_place`` entry. The routers keep the
request half (scope, the ``BackgroundJob`` row, the enqueue) and build the spec.

Inpaint and detection crop are not here on purpose: their count keys, crop's
stage/flush/swap and inpaint's model lease would each need a flag. All three
share one copy-mode order, though: commit the derivative row, then cut its
thumbnail as a counted best-effort step (PM-013 Tier 1).

The spec's callables are built in the router and look up the router's own
globals when called, so a test that patches ``upscale_image_sync`` or
``apply_lut_sync`` on the router module still reaches the job.
"""
import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import undefer

from backend.config import settings
from backend.job_outcome import failure_summary, note_failure
from backend.ml import device as _device
from backend.licenses import copy_provenance
from backend.models import BackgroundJob, Image
from backend.services import version_service
from backend.services.dataset_service import refresh_stats, refresh_stats_after_abort
from backend.services.image_service import generate_thumbnail
from backend.services.label_service import copy_labels
from backend.utils import (
    OUT_OF_TREE_MSG,
    chunked,
    contained_path,
    normalize_image_format,
    record_in_place,
    slugify_filename,
    thumbnail_path_for,
    unique_filename_with_thumb,
)
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EditSpec:
    """What one in-place pixel edit job does differently from the other."""

    job_type: str
    """``batch_upscale`` / ``batch_lut``. Every progress event carries it, and
    the frontend's job-type sets key on it, so it never changes."""
    context: str
    """``"Upscale"`` / ``"LUT"``: log prefixes and the path gates' context."""
    start_verb: str
    """The start message, before the filename: ``"Upscaling"``."""
    action: str
    """``failure_summary``'s verb phrase: ``"could not be upscaled"``."""
    suffix: Callable[[], str]
    """The copy's stem suffix, such as ``"_up4x"``. Called once, in the job."""
    process: Callable[[str, str], dict]
    """``(src, dest) -> info``, run in an executor. ``info`` holds ``width``,
    ``height``, ``file_size_bytes``, ``format`` and, when the PNG fallback
    moved the path, ``out_path``."""
    history_op: str
    """``record_in_place``'s operation name."""
    history_params: dict = field(default_factory=dict)
    """``record_in_place``'s keyword params, such as the model's filename."""
    gpu: bool = True
    """Whether ``process`` runs on the accelerator, and so on ``device.run_on_gpu``'s
    single thread rather than the default pool (PM-035). The structural guard
    cannot see through ``spec.process``, so this flag is the rule's only enforcer
    here; it defaults to True so a forgotten flag lands on the safe side. LUT
    grading is numpy and says ``gpu=False``."""


async def run_image_edit_job(
    job_id: str, image_ids: list[str], *, replace: bool, spec: EditSpec
) -> None:
    """Run the edit over ``image_ids``, refreshing stats even on an abort.

    The loop's tail refreshes on completion and on a soft cancel; a raise
    outside the per-image ``try``, or a hard cancel, skips it with earlier
    images already committed, so this refreshes every dataset touched so far.
    """
    # Every dataset whose stored stats the run moved. A set, not the request's
    # dataset: a selection can span datasets (PM-029).
    touched: set[str] = set()
    try:
        await _edit_loop(job_id, image_ids, replace, spec, touched)
    except BaseException:
        await refresh_stats_after_abort(touched, context=spec.context)
        raise


async def _edit_loop(
    job_id: str, image_ids: list[str], replace: bool, spec: EditSpec, touched: set[str]
) -> None:
    from backend.database import AsyncSessionLocal
    from backend.workers.progress import broadcaster

    def _progress(i: int, total: int, img: Image, **extra) -> dict:
        return {
            "type": "progress", "job_id": job_id, "job_type": spec.job_type,
            "status": "running", "done": i, "total": total,
            "percent": round(i / total * 100, 1),
            "current_item": img.filename,
            **extra,
        }

    async with AsyncSessionLocal() as session:
        # undefer only where it is needed: a copy carries the parent's
        # provenance, source_meta included, and a deferred lazy load would
        # raise on this async session. The replace branch never calls
        # `copy_provenance`, so undeferring there would load a scraper's full
        # raw payload for the whole batch and discard it. Chunked, because a
        # whole-dataset run can pass SQLite's bind limit.
        images = []
        for chunk in chunked(image_ids):
            query = select(Image).where(Image.id.in_(chunk))
            if not replace:
                query = query.options(undefer(Image.source_meta))
            result = await session.execute(query)
            images.extend(result.scalars().all())
        loop = asyncio.get_running_loop()

        # The whole outcome of the run, written to `result_data` at the end.
        # `skipped` is seeded the way `video_reextract` seeds its own: a row
        # can be deleted between enqueue and run, and asking for an image that
        # no longer exists is a skip, not a failure. The loop's two `continue`
        # branches are invisible without this, so a run that failed on 40 of
        # 50 images used to toast a flat "LUT applied".
        #
        # `thumbnails_stale` counts edited images whose *preview* could not be
        # recut. The image itself is correct and committed, but the gallery
        # keeps rendering the old tile, and the realistic trigger (a full
        # volume, a read-only thumbnails/) hits every image in the run.
        #
        # `missing` is the vanished-row part of `skipped`, kept apart so a
        # one-image run can say *which* skip it was: the rest of `skipped` is
        # the replace-mode name collision below.
        missing = len(image_ids) - len(images)
        counts = {
            "processed": 0,
            "skipped": missing,
            "missing": missing,
            "failed": 0,
            "thumbnails_stale": 0,
        }
        # What failed and why, for `failure_summary`: the live `Failed:`
        # message is gone on reload, and the job still ends `completed`
        # (PM-023).
        failures: list[dict] = []

        suffix = spec.suffix()
        last_image_id: str | None = None

        # Occupied/planned thumbnail stems for the copy path, keyed by
        # thumbnail directory: matched images can span multiple datasets (each
        # with its own thumbnails/ dir), so a single flat set would false-share
        # stems across datasets. Built lazily per dir inside the loop;
        # planned_by_dir accumulates across iterations (mutated by
        # unique_filename_with_thumb per its contract).
        occupied_by_dir: dict[Path, set[str]] = {}
        planned_by_dir: dict[Path, set[str]] = {}

        # Copy-mode derivatives: parent id -> new id, drained once after the
        # loop. A same-dataset derivative carries its parent's labels for the
        # same reason it carries `copy_provenance` — "this image is a reject"
        # is a fact about the picture, not about the file.
        derivative_ids: dict[str, str] = {}

        cancelled = False
        total = len(images)
        for i, img in enumerate(images):
            if job_queue.cancel_requested(job_id):
                cancelled = True
                break
            # Emit BEFORE the edit runs: on CPU a single upscale can take
            # minutes, and without this a small job sits at "Starting…" the
            # whole time, looking hung.
            await broadcaster.emit(job_id, _progress(
                i, total, img, message=f"{spec.start_verb} {img.filename}…",
            ))
            # Gate before anything reads or writes through the stored path: a
            # link in images/ would otherwise have the edit overwrite a file
            # outside the datasets folder, and `protect_file_before_overwrite`
            # refuses that path without stopping the write. A gate only — the
            # row's own spelling is kept below (`contained_path`'s docstring).
            if contained_path(
                img.file_path, settings.datasets_dir, context=spec.context, ident=img.id
            ) is None:
                await broadcaster.emit(job_id, _progress(
                    i + 1, total, img, message=f"Failed: {OUT_OF_TREE_MSG}",
                ))
                counts["failed"] += 1
                note_failure(failures, img.filename, OUT_OF_TREE_MSG)
                continue
            src_path = Path(img.file_path)
            dest_path_str: str

            # Where `spec.process` will actually write: for .gif/.bmp/.tiff/
            # .avif that is a *different* suffix, PNG being the fallback
            # format. One call answers both modes — the replace branch needs
            # the full path to check for a squatter, the copy branch needs the
            # suffix so it reserves the name it will use.
            _fmt, planned_out = normalize_image_format(src_path.suffix, str(src_path))
            out_suffix = Path(planned_out).suffix

            if replace:
                dest_path_str = str(src_path)
            else:
                dest_images = src_path.parent
                dest_thumb_dir = src_path.parent.parent / "thumbnails"
                if dest_thumb_dir not in occupied_by_dir:
                    occupied_by_dir[dest_thumb_dir] = (
                        {p.stem for p in dest_thumb_dir.glob("*.webp")}
                        if dest_thumb_dir.exists() else set()
                    )
                occupied_thumb_stems = occupied_by_dir[dest_thumb_dir]
                planned_thumb_stems = planned_by_dir.setdefault(dest_thumb_dir, set())
                dest_stem = slugify_filename(src_path.stem + suffix)
                existing = await session.execute(
                    select(Image.filename).where(
                        Image.dataset_id == img.dataset_id,
                        Image.filename.like(f"{dest_stem}%"),
                    )
                )
                db_names: set[str] = {r[0] for r in existing.all()}
                # `out_suffix`, not the source's: reserving the name under the
                # extension that will actually be written is what makes both
                # the db_names and the on-disk check apply to the real path.
                # `unique_filename` only stats the suffix it is handed, so a
                # copy of shot.bmp would otherwise overwrite an existing
                # shot_lut.png, registered or not.
                new_filename = unique_filename_with_thumb(
                    dest_images, dest_stem, out_suffix, db_names,
                    occupied_thumb_stems, planned_thumb_stems,
                )
                dest_path_str = str(dest_images / new_filename)

            if replace:
                # The collision has to be caught before the write, not after:
                # an unregistered file hand-dropped into images/ has no DB row
                # guarding it, and by the time the save has run it is gone.
                if planned_out != str(src_path) and Path(planned_out).exists():
                    logger.warning(
                        "%s: %s would be written as %s, which already exists on disk — skipped",
                        spec.context, img.filename, Path(planned_out).name,
                    )
                    await broadcaster.emit(job_id, _progress(
                        i + 1, total, img,
                        message=f"Skipped: {Path(planned_out).name} already exists on disk",
                    ))
                    counts["skipped"] += 1
                    continue
                await version_service.protect_file_before_overwrite(img.id, img.file_path, session)
                await session.commit()  # persist the COW hash backfill before the overwrite

            try:
                if spec.gpu:
                    info = await _device.run_on_gpu(spec.process, str(src_path), dest_path_str)
                else:
                    info = await loop.run_in_executor(
                        None, spec.process, str(src_path), dest_path_str,
                    )
            except Exception as exc:
                logger.error("%s failed for %s: %s", spec.context, img.filename, exc)
                await broadcaster.emit(job_id, _progress(
                    i + 1, total, img, message=f"Failed: {exc}",
                ))
                counts["failed"] += 1
                note_failure(failures, img.filename, str(exc))
                continue

            # The PNG fallback may have moved the output path.
            actual_out_path = info.get("out_path", dest_path_str)

            if replace:
                superseded: Path | None = None
                # A LUT returns the size it was given, so for it this rewrites
                # the same values; an upscale changes them.
                img.width = info["width"]
                img.height = info["height"]
                img.file_size_bytes = info["file_size_bytes"]
                # A grade or an upscale changes the picture duplicate detection
                # hashes, so the row's pHash would describe the pre-edit pixels
                # (GPT-016). Both workers hash their output in memory.
                img.phash = info["phash"]
                # `updated_at` is stamped by `record_in_place` below.
                if Path(actual_out_path) != src_path:
                    # The PNG fallback wrote a *different* file. Without this
                    # the row keeps pointing at the stale original, which is
                    # also left on disk — PM-009.
                    #
                    # A pure extension change moves nothing derived: the stem
                    # is unchanged, so the thumbnail ({stem}.webp) and the
                    # caption sidecar ({stem}.txt) stay exactly where they
                    # are, and no other row can hold the name because every
                    # image-name-picking site rejects an occupied *stem* in
                    # any extension.
                    #
                    # The original is unlinked in the epilogue below, after
                    # the commit: `unlink` is fallible, and a failure here
                    # would leave the write done and the row uncommitted.
                    superseded = src_path
                    img.filename = Path(actual_out_path).name
                    img.file_path = actual_out_path
                    img.format = info["format"]
                # Writes `processing_history` *and* `scores_stale` — the edit
                # changed the pixels the scores were measured on, and an
                # upscale the resolution `blur_score` is measured against. Pure
                # dict building, so it cannot raise between the overwrite above
                # and the commit below (PM-013).
                record_in_place(img, spec.history_op, **spec.history_params)
                # Nothing fallible between the (already-done) overwrite and
                # this commit — a raise before here would roll the row back
                # onto a file that no longer exists (PM-013). `touched` first,
                # so a hard cancel landing in the commit still refreshes; early
                # is harmless, since an unchanged dataset refreshes the same.
                touched.add(img.dataset_id)
                await session.commit()

                # --- epilogue: best-effort, cannot undo the edit ---
                # `expire_on_commit=False` (backend/database.py) keeps `img`
                # readable after the commit without a refresh.
                if superseded is not None:
                    try:
                        superseded.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning(
                            "%s: could not remove superseded %s: %s",
                            spec.context, superseded.name, exc,
                        )
                # `generate_thumbnail` mkdirs its parent, so an out-of-tree
                # `thumbnail_path` is a write primitive: gated like
                # `file_path`, as inpaint does. A refusal is a stale tile.
                thumb_target = (
                    contained_path(
                        img.thumbnail_path, settings.datasets_dir,
                        context=f"{spec.context} thumbnail", ident=img.id,
                    )
                    if img.thumbnail_path else None
                )
                if img.thumbnail_path and thumb_target is None:
                    counts["thumbnails_stale"] += 1
                elif thumb_target is not None:
                    try:
                        await loop.run_in_executor(
                            None, generate_thumbnail, actual_out_path, str(thumb_target)
                        )
                    except Exception as exc:
                        # A stale thumbnail is cosmetic; the edited image is
                        # committed and serves. Counted rather than merely
                        # logged so the run can say so: TopBar reads this
                        # count and points at Bulk Edit → Thumbnails.
                        counts["thumbnails_stale"] += 1
                        logger.warning(
                            "%s: thumbnail for %s could not be regenerated: %s",
                            spec.context, img.filename, exc,
                        )
                last_image_id = img.id
            else:
                dest_path = Path(actual_out_path)
                thumb_path = thumbnail_path_for(actual_out_path)
                new_img = Image(
                    dataset_id=img.dataset_id,
                    filename=dest_path.name,
                    original_filename=img.original_filename,
                    subfolder=img.subfolder,
                    file_path=actual_out_path,
                    thumbnail_path=thumb_path,
                    width=info["width"],
                    height=info["height"],
                    file_size_bytes=info["file_size_bytes"],
                    format=info["format"],
                    # Without it the copy never enters a duplicate scan, which
                    # selects `phash IS NOT NULL`.
                    phash=info["phash"],
                    # A same-dataset derivative keeps its parent's source/license.
                    **copy_provenance(img),
                )
                # PM-013 Tier 1, as inpaint's copy branch: the row that
                # describes the file now on disk is committed *before* anything
                # fallible runs. Cutting the thumbnail first, with the rows only
                # flushed, let one `OSError` propagate out of the loop, fail the
                # job and roll back **every** derivative row of the run while
                # their files stayed on disk. `touched` first, as above.
                session.add(new_img)
                touched.add(img.dataset_id)
                await session.commit()
                # `expire_on_commit=False` (backend/database.py), so the id is
                # readable after the commit without a refresh.
                derivative_ids[img.id] = new_img.id
                last_image_id = new_img.id
                # The thumbnail path is derived from the gated source folder,
                # so it needs no gate of its own (inpaint's copy branch).
                try:
                    await loop.run_in_executor(
                        None, generate_thumbnail, actual_out_path, thumb_path
                    )
                except Exception as exc:
                    # Same epilogue contract as the replace branch: the row is
                    # committed and the image serves; only the tile is missing,
                    # and the count is what lets TopBar say so.
                    counts["thumbnails_stale"] += 1
                    logger.warning(
                        "%s: thumbnail for %s could not be generated: %s",
                        spec.context, dest_path.name, exc,
                    )

            await broadcaster.emit(job_id, _progress(
                i + 1, total, img, image_id=last_image_id,
            ))
            counts["processed"] += 1

        # After the loop, so a cancelled run still labels what it did copy.
        await copy_labels(session, derivative_ids)

        job_row = await session.get(BackgroundJob, job_id)
        if job_row:
            job_row.result_data = {
                **counts,
                "failed_count": counts["failed"],
                "failure_summary": failure_summary(
                    counts["failed"], failures, action=spec.action
                ),
            }
        # Above `raise_if_cancelled`, so a cancelled run keeps the counts for
        # everything it did manage — including any stale thumbnail.
        await session.commit()

        # A replace changed a file's size and a copy added a row, and both
        # are stored on the dataset. After the final commit, so a failed
        # refresh cannot roll back the copy rows, their labels or the counts
        # (PM-013); before the cancel raise, so a cancelled run still
        # refreshes what it did. Detection crop's epilogue, copied.
        for ds_id in touched:
            try:
                await refresh_stats(session, ds_id)
            except Exception:
                logger.warning("%s: could not refresh stats for %s", spec.context, ds_id, exc_info=True)
                await session.rollback()
        if cancelled:
            job_queue.raise_if_cancelled(job_id)
