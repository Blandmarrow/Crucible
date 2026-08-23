"""Watermark removal — paint detected regions out of an image with LaMa.

The mask comes from existing `Detection` rows, so the flow is two steps: run a
grounding detection pass for "watermark" first, then run this over what it found.
Nothing here runs a detector.

Structurally this is `routers/lut.py`'s loop with `routers/detection.py`'s
crop-to-detection scope resolution — the per-thumbnail-dir collision dicts and
the `result_data` placement are shared with both. Two things deliberately are
**not**: the copy branch commits its row before cutting the thumbnail (the
PM-013 Tier-1 shape, where both siblings still carry Tier 3), and every stored
path goes through `contained_path` before it is read or written.
"""

import asyncio
import io
import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from backend.config import settings
from backend.database import get_db
from backend.licenses import copy_provenance
from backend.ml.lama_inpainter import inpaint_image_sync
from backend.ml.mask_utils import rasterize_detections
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
from backend.models.detection import Detection
from backend.schemas.inpaint import InpaintRunRequest
from backend.services import version_service
from backend.services.image_service import generate_thumbnail
from backend.services.label_service import copy_labels
from backend.utils import (
    ALLOWED_FLAG_KEYS,
    chunked,
    contained_path,
    normalize_image_format,
    normalize_subfolder,
    record_in_place,
    slugify_filename,
    thumbnail_path_for,
    unique_filename_with_thumb,
)
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/inpaint", tags=["inpaint"])


async def _fetch_masks_by_image(
    db: AsyncSession, image_ids: list[str], labels: list[str] | None
) -> dict[str, list[tuple[int, str | None, list[float]]]]:
    """Batch-fetch `(detection_id, mask_json, bbox)` triples keyed by image id.

    `_fetch_bboxes_by_image`'s shape with the mask and the row id added: the mask
    is what gets rasterized (bbox-only detections fall back to a rectangle inside
    `rasterize_detections`), and the id is what gets deleted once the region it
    describes has been painted away. Chunked for SQLite's bind-parameter ceiling.
    """
    by_image: dict[str, list[tuple[int, str | None, list[float]]]] = {}
    for chunk in chunked(image_ids):
        query = select(Detection.id, Detection.image_id, Detection.mask, Detection.bbox).where(
            Detection.image_id.in_(chunk)
        )
        if labels:
            query = query.where(Detection.label.in_(labels))
        result = await db.execute(query)
        for row in result.all():
            by_image.setdefault(row.image_id, []).append((row.id, row.mask, row.bbox))
    return by_image


async def _fetch_detection_ids_by_image(
    db: AsyncSession, image_ids: list[str]
) -> dict[str, set[int]]:
    """Every detection id per image, **unfiltered by label**.

    The companion to `_fetch_masks_by_image`, and deliberately not folded into
    it: the mask is built from the label-filtered rows, but deciding whether
    `has_watermark` may be cleared needs to know what the run is *not* painting.
    One batched query for the whole run, not one per image.
    """
    by_image: dict[str, set[int]] = {}
    for chunk in chunked(image_ids):
        result = await db.execute(
            select(Detection.id, Detection.image_id).where(Detection.image_id.in_(chunk))
        )
        for row in result.all():
            by_image.setdefault(row.image_id, set()).add(row.id)
    return by_image


def _dilate(mask, px: int):
    """Grow a rasterized binary mask by `px` pixels, in place of a morphology dep.

    `MaxFilter` over an odd window is an exact square dilation, and the mask is
    binary 0/255 so the result stays binary.

    Filtered over the mask's bounding box grown by `px`, not over the whole
    canvas. `MaxFilter` is a `RankFilter`: it selects over all k² pixels of the
    window for every output pixel, so the cost scales with
    `canvas_area × (2·px+1)²` — measured 2.0 s at the default `px=6` on a
    4000×3000 canvas and 168 s at the schema's max of 64. Dilation cannot reach
    further than `px` from a white pixel, so cropping to that region, filtering
    it and pasting it back is **exactly** equivalent: outside the crop every
    window sees black in both runs, the crop's own border columns are black by
    construction, and where the crop meets the image border the padding is the
    one the full-canvas run uses. `backend/tests/test_inpaint_dilate.py` pins the
    equivalence.

    Mutates and returns `mask` — the caller owns a freshly rasterized image.
    """
    if px <= 0:
        return mask
    from PIL import ImageFilter
    bbox = mask.getbbox()
    if bbox is None:
        # An all-black mask dilates to itself; `getbbox()` gives no region to
        # crop to, and the full-canvas filter would be a very slow no-op.
        return mask
    w, h = mask.size
    box = (
        max(0, bbox[0] - px), max(0, bbox[1] - px),
        min(w, bbox[2] + px), min(h, bbox[3] + px),
    )
    region = mask.crop(box)
    grown = region.filter(ImageFilter.MaxFilter(2 * px + 1))
    mask.paste(grown, (box[0], box[1]))
    region.close()
    grown.close()
    return mask


def _build_mask_png(
    triples: list[tuple[int, str | None, list[float]]],
    width: int,
    height: int,
    dilate_px: int,
) -> bytes:
    """Rasterize → dilate → PNG-encode the paint mask. **Executor thread only.**

    All three steps are full-resolution Pillow work with no `await` in them, so
    running this on the event loop freezes the whole server — every request, the
    SSE progress stream included — for its duration. `export_service`'s
    `_write_mask` goes through `run_in_executor` for exactly this reason, and
    `docs/dev/ml-models.md` states the rule for every inference path.

    `rasterize_detections`, never `compose_loss_mask`: the latter's empty-include
    case returns full white, which here would repaint the entire image. This one
    fails safe — empty geometry rasterizes to all black and nothing is painted.
    """
    mask_img = rasterize_detections([(m, b) for _id, m, b in triples], width, height)
    mask_img = _dilate(mask_img, dilate_px)
    buf = io.BytesIO()
    mask_img.save(buf, format="PNG")
    mask_img.close()
    return buf.getvalue()


@router.post("/run")
async def run_inpaint(body: InpaintRunRequest, db: AsyncSession = Depends(get_db)):
    # Scope resolution: the standard ids > dataset+subfolder+flags triple. No
    # `ensure_not_busy` — background jobs are serialized by the single job queue
    # and no bulk job takes the busy flag (services/dataset_busy.py).
    if body.image_ids is not None:
        result = await db.execute(select(Image.id).where(Image.id.in_(body.image_ids)))
        image_ids = [r[0] for r in result.all()]
    else:
        q = select(Image.id).where(Image.dataset_id == body.dataset_id)
        if body.subfolder is not None:
            q = q.where(Image.subfolder == normalize_subfolder(body.subfolder))
        if body.quality_flags:
            valid_flags = [f for f in body.quality_flags if f in ALLOWED_FLAG_KEYS]
            if valid_flags:
                q = q.where(and_(*[Image.quality_flags[f].as_boolean().is_not(True) for f in valid_flags]))
        result = await db.execute(q)
        image_ids = [r[0] for r in result.all()]

    # Normalize the destination subfolder once, up front, so a bad path 400s
    # immediately instead of failing inside the background job.
    if body.dest_subfolder is not None:
        body.dest_subfolder = normalize_subfolder(body.dest_subfolder)

    # Pre-filter to the images that actually have a matching detection, so
    # `total_items` is honest about how much work the job has.
    by_image = await _fetch_masks_by_image(db, image_ids, body.labels)
    matched_ids = [i for i in image_ids if i in by_image]
    skipped = len(image_ids) - len(matched_ids)
    total = len(matched_ids)

    auto_label = f"Remove watermark — {total} image{'s' if total != 1 else ''}"
    job = BackgroundJob(
        job_type="batch_inpaint",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=total,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    cfg = body.model_dump()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.workers.progress import broadcaster

        async with AsyncSessionLocal() as session:
            replace = cfg["replace"]
            dest_subfolder = cfg["dest_subfolder"]  # None = inherit source subfolder
            dilate_px = cfg["dilate_px"]
            labels = cfg["labels"]

            images = []
            for chunk in chunked(matched_ids):
                # undefer only in the copy branch: a new-file inpaint copies its
                # parent's provenance, source_meta included, and a deferred lazy
                # load on an async session raises MissingGreenlet. The replace
                # branch never calls `copy_provenance`, so undeferring there would
                # load a scraper's full raw payload for the whole batch and
                # discard it.
                query = select(Image).where(Image.id.in_(chunk))
                if not replace:
                    query = query.options(undefer(Image.source_meta))
                result = await session.execute(query)
                images.extend(result.scalars().all())
            masks_by_image = await _fetch_masks_by_image(session, matched_ids, labels)
            # Unfiltered, for the `has_watermark` decision below: the flag may only
            # be cleared once *nothing* detected is left on the image.
            all_det_ids = await _fetch_detection_ids_by_image(session, matched_ids)
            loop = asyncio.get_running_loop()

            # `skipped_no_detection` is seeded with the rows that vanished between
            # enqueue and run: asking for an image that no longer exists is a skip,
            # not a failure. It keeps only its two real causes — a squatting
            # filename is `skipped_name_taken`, a separate key, because blaming
            # the detection pass for a stray file on disk sends the user looking
            # in the wrong place. `thumbnails_stale` is the epilogue's own failure
            # — the image is committed and serves, only the gallery tile is old.
            counts = {
                "inpainted": 0,
                "skipped_no_detection": len(matched_ids) - len(images),
                "skipped_name_taken": 0,
                "failed": 0,
                "thumbnails_stale": 0,
            }

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

            last_image_id: str | None = None
            cancelled = False

            if images:
                # Load once, before the loop: this is where eviction and the
                # first-run 196 MB weight download happen, and both want the async
                # side. `inpaint_image_sync` then reads the resident entry.
                await model_manager.load_lama(job_id, loop, cfg["dataset_id"])

            for i, img in enumerate(images):
                # The non-raising check, so the counts written below survive.
                if job_queue.cancel_requested(job_id):
                    cancelled = True
                    break
                # LaMa on CPU takes seconds to minutes per image; without a
                # "starting" emit a small job looks hung for its whole duration.
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_inpaint",
                    "status": "running", "done": i, "total": len(images),
                    "percent": round(i / len(images) * 100, 1),
                    "current_item": img.filename,
                    "message": f"Removing watermark from {img.filename}…",
                })

                triples = masks_by_image.get(img.id, [])
                if not triples:
                    counts["skipped_no_detection"] += 1
                    continue

                # Gate the stored path before anything reads or writes through it:
                # `protect_file_before_overwrite` below copies the bytes into
                # `{ds}/.versions/objects/`, so an out-of-tree `file_path` is an
                # arbitrary-file *read* primitive even before the overwrite, and
                # the copy branch derives its destination from this path's parent.
                # The non-raising per-row form — `safe_dataset_path`'s 403 is wrong
                # inside a job loop, where one bad row must not stop its
                # neighbours. Everything below uses the *resolved* path it hands
                # back, never the raw string.
                src_path = contained_path(
                    img.file_path, settings.datasets_dir, context="Inpaint", ident=img.id
                )
                if src_path is None:
                    counts["failed"] += 1
                    continue

                # Rasterize → dilate → PNG-encode in an executor: it is
                # full-resolution Pillow work with no await in it, and on the event
                # loop it freezes every other request for its duration.
                mask_bytes = await loop.run_in_executor(
                    None, _build_mask_png, triples, img.width, img.height, dilate_px
                )

                # Where `inpaint_image_sync` will actually write. For .gif/.bmp/
                # .tiff/.avif that is a *different* path — PNG is the fallback
                # format. Computed once for both modes: replace needs the whole
                # path to check for a squatter, copy needs the suffix.
                _fmt, planned_out = normalize_image_format(src_path.suffix, str(src_path))
                out_suffix = Path(planned_out).suffix

                if replace:
                    # The collision has to be caught before the write, not after:
                    # an unregistered file hand-dropped into images/ has no DB row
                    # guarding it, and by the time the save has run it is gone.
                    if planned_out != str(src_path) and Path(planned_out).exists():
                        logger.warning(
                            "Inpaint: %s would be written as %s, which already exists "
                            "on disk — skipped", img.filename, Path(planned_out).name,
                        )
                        await broadcaster.emit(job_id, {
                            "type": "progress", "job_id": job_id, "job_type": "batch_inpaint",
                            "status": "running", "done": i + 1, "total": len(images),
                            "percent": round((i + 1) / len(images) * 100, 1),
                            "current_item": img.filename,
                            "message": f"Skipped: {Path(planned_out).name} already exists on disk",
                        })
                        counts["skipped_name_taken"] += 1
                        continue
                    dest_path_str = str(src_path)
                    await version_service.protect_file_before_overwrite(img.id, str(src_path), session)
                    # Not optional: the COW hash backfill is only flushed, and
                    # without this commit a crash mid-overwrite leaves the snapshot
                    # claiming the content never changed.
                    await session.commit()
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
                    dest_stem = slugify_filename(src_path.stem + "_nowm")
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
                    new_filename = unique_filename_with_thumb(
                        dest_images, dest_stem, out_suffix, db_names,
                        occupied_thumb_stems, planned_thumb_stems,
                    )
                    dest_path_str = str(dest_images / new_filename)

                try:
                    info = await loop.run_in_executor(
                        None, inpaint_image_sync,
                        str(src_path), dest_path_str, mask_bytes, replace,
                    )
                except Exception as exc:
                    # One bad image never fails the run.
                    logger.error("Inpaint failed for %s: %s", img.filename, exc)
                    await broadcaster.emit(job_id, {
                        "type": "progress", "job_id": job_id, "job_type": "batch_inpaint",
                        "status": "running", "done": i + 1, "total": len(images),
                        "percent": round((i + 1) / len(images) * 100, 1),
                        "current_item": img.filename,
                        "message": f"Failed: {exc}",
                    })
                    counts["failed"] += 1
                    continue

                actual_out_path = info.get("out_path", dest_path_str)

                if replace:
                    superseded: Path | None = None
                    img.file_size_bytes = info["file_size_bytes"]
                    # Inpainting rewrites pixels without moving a single edge, so
                    # `width`/`height` are unchanged and the detections need no
                    # `remap_detections_for_crop`. `phash` is *not* unchanged, and
                    # it is the only thing duplicate detection keys on.
                    img.phash = info["phash"]
                    if Path(actual_out_path) != src_path:
                        # `normalize_image_format` falls back to PNG for .gif,
                        # .bmp, .tiff and .avif — all in IMAGE_EXTENSIONS — so a
                        # replace-mode paint of one of those writes a *different*
                        # file. Without this the row keeps pointing at the stale
                        # original, which is also left on disk.
                        #
                        # A pure extension change moves nothing derived: the stem
                        # is unchanged, so the thumbnail ({stem}.webp) and the
                        # caption sidecar ({stem}.txt) stay exactly where they are.
                        #
                        # The original is unlinked in the epilogue below, after the
                        # commit: `unlink` is fallible, and a failure here would
                        # leave the write done and the row uncommitted.
                        superseded = src_path
                        img.filename = Path(actual_out_path).name
                        img.file_path = actual_out_path
                        img.format = info["format"]

                    # The detections have been painted over: the regions they name
                    # no longer contain anything. Deleting them in the same
                    # transaction as the pixels is what stops the panel from
                    # offering to crop to a watermark that is gone.
                    painted_ids = {d for d, _m, _b in triples}
                    await session.execute(
                        delete(Detection).where(Detection.id.in_(painted_ids))
                    )
                    # `has_watermark` is cleared **iff nothing detected remains**.
                    # Crucible has no vocabulary of "watermark labels" —
                    # `detection._apply_watermark_flag` sets the flag from
                    # `bool(detections)` on a watermark-sync grounding run, whatever
                    # the phrase was — so a run scoped to some other label cannot
                    # tell whether what survived is a watermark. Clearing anyway
                    # would let the image escape both the *Flagged: watermark*
                    # filter and export's `exclude_flags`, and ship with the
                    # watermark visible. When nothing is cleared the column is not
                    # written at all, rather than reassigned to an equal dict.
                    if not (all_det_ids.get(img.id, set()) - painted_ids):
                        # Copy-then-reassign: SQLAlchemy compares JSON columns by
                        # equality, so mutating the loaded dict in place looks
                        # unchanged and the UPDATE is silently skipped
                        # (CLAUDE.md § Key invariants).
                        flags = dict(img.quality_flags or {})
                        flags["has_watermark"] = False
                        img.quality_flags = flags

                    # Writes `processing_history` *and* `scores_stale` — the paint
                    # changed the pixels `watermark_score` was measured on, so the
                    # number is flagged rather than silently trusted. Pure dict
                    # building, so it cannot raise between the overwrite above and
                    # the commit below (PM-013).
                    record_in_place(img, "inpaint", labels=labels, dilate_px=dilate_px)
                    # Nothing fallible between the (already-done) overwrite and this
                    # commit — a raise before here would roll back the row of every
                    # image the loop has already overwritten on disk (PM-013).
                    await session.commit()

                    # --- epilogue: best-effort, cannot undo the paint ---
                    # `expire_on_commit=False` (backend/database.py) keeps `img`
                    # readable after the commit without a refresh.
                    if superseded is not None:
                        try:
                            superseded.unlink(missing_ok=True)
                        except OSError as exc:
                            logger.warning(
                                "Inpaint: could not remove superseded %s: %s",
                                superseded.name, exc,
                            )
                    # `generate_thumbnail` mkdirs its parent, so an out-of-tree
                    # `thumbnail_path` is an arbitrary-file *write* primitive —
                    # the reasoning CLAUDE.md gives for `images.bulk_thumbnails`,
                    # where the gate likewise guards no unlink. A refusal is a
                    # stale tile, which is exactly what the count already means.
                    thumb_target = (
                        contained_path(
                            img.thumbnail_path, settings.datasets_dir,
                            context="Inpaint thumbnail", ident=img.id,
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
                            # A stale thumbnail is cosmetic; the painted image is
                            # committed and serves. Counted rather than merely
                            # logged so the run can say so: TopBar reads this count
                            # and points at Bulk Edit → Thumbnails.
                            counts["thumbnails_stale"] += 1
                            logger.warning(
                                "Inpaint: thumbnail for %s could not be regenerated: %s",
                                img.filename, exc,
                            )
                    last_image_id = img.id
                else:
                    actual_dest = Path(actual_out_path)
                    thumb_path = thumbnail_path_for(actual_out_path)
                    new_img = Image(
                        dataset_id=img.dataset_id,
                        filename=actual_dest.name,
                        original_filename=img.original_filename,
                        subfolder=dest_subfolder if dest_subfolder is not None else img.subfolder,
                        file_path=actual_out_path,
                        thumbnail_path=thumb_path,
                        width=info["width"],
                        height=info["height"],
                        file_size_bytes=info["file_size_bytes"],
                        format=info["format"],
                        phash=info["phash"],
                        # A painted derivative keeps its parent's source and license.
                        **copy_provenance(img),
                    )
                    # PM-013 Tier 1: the row that describes the file on disk is
                    # committed *before* anything fallible runs. Cutting the
                    # thumbnail first — the Tier-3 shape four sibling routers still
                    # carry — lets an `OSError` on image 300 of 500 propagate out of
                    # `_run`, where `job_queue` marks the job failed from a separate
                    # session and this one rolls back **every** uncommitted
                    # derivative row while their `_nowm` files stay on disk.
                    session.add(new_img)
                    await session.commit()
                    # `expire_on_commit=False` (backend/database.py), so the id is
                    # readable after the commit without a refresh.
                    derivative_ids[img.id] = new_img.id
                    last_image_id = new_img.id
                    try:
                        await loop.run_in_executor(
                            None, generate_thumbnail, actual_out_path, thumb_path
                        )
                    except Exception as exc:
                        # Same epilogue contract as the replace branch: the row is
                        # committed and the image serves, only the gallery tile is
                        # missing, and the count is what lets TopBar say so.
                        counts["thumbnails_stale"] += 1
                        logger.warning(
                            "Inpaint: thumbnail for %s could not be generated: %s",
                            actual_dest.name, exc,
                        )

                counts["inpainted"] += 1
                await broadcaster.emit(job_id, {
                    "type": "progress", "job_id": job_id, "job_type": "batch_inpaint",
                    "status": "running", "done": i + 1, "total": len(images),
                    "percent": round((i + 1) / len(images) * 100, 1),
                    "current_item": img.filename,
                    "image_id": last_image_id,
                })

            # After the loop, so a cancelled run still labels what it did copy.
            await copy_labels(session, derivative_ids)

            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = counts
            # Above `raise_if_cancelled`, so a cancelled run keeps the counts for
            # everything it did manage — and so the completion handler that
            # re-fetches the job can see `thumbnails_stale`.
            await session.commit()
            if cancelled:
                job_queue.raise_if_cancelled(job_id)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": total, "skipped": skipped}
