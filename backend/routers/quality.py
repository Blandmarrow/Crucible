import asyncio
import base64
import contextlib
import functools
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ml import device as _device
from backend.ml.model_manager import ModelInUseError, model_manager
from backend.ml.similarity_scorer import (
    DEFAULT_DINO_LAYER,
    STYLE_CLIP_WEIGHT,
    STYLE_DINO_WEIGHT,
)
from backend.models import BackgroundJob, Image, StyleSimilarityRun, Video
from backend.models.style_run import REFERENCE_IDS_STORED_MAX
from backend.services.image_delete import unlink_files_for_rows
from backend.services.dataset_busy import ensure_not_busy
from backend.services.dataset_service import get_style_distribution, refresh_stats
from backend.services.duplicate_service import (
    apply_duplicate_groups,
    prune_orphaned_duplicate_flags,
)
from backend.utils import chunked, normalize_subfolder, score_columns
from backend.workers.job_queue import job_queue, run_job_config, run_job_label

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quality", tags=["quality"])

# Per-layer DINOv2 blob layout: 12 transformer layers × 768 dims × float16.
_DINO_LAYERS = 12
_DINO_DIM = 768

# The descriptor tail every style-similarity branch returns beside `updated` and
# `skipped`, so `_record_style_run` reads what the run *did* rather than re-reading
# `body` — the same argument as `aesthetic_marker` in `score_quality`. This is the
# shape for a mode that neither blends nor names a numbered layer (`clip`, and
# `dino` on the post-layernorm final embedding).
_UNBLENDED_FINAL = {"dino_layer": None, "clip_weight": None, "dino_weight": None}

# Candidates per keyset page in `_score_style_similarity`. The scan runs inside the
# HTTP request, so one page is all it may hold: 2,000 × the 18 KB per-layer blob is
# about 36 MB, where the whole set was ~1.8 GB at 100k images. A module constant so
# `test_style_similarity_paging.py` can shrink it and run the loop over several pages.
_STYLE_PAGE_SIZE = 2000

# The six columns the technical block of the scoring loop writes, in one go.
_TECHNICAL_SCORE_COLUMNS = frozenset({
    "blur_score", "noise_score", "uniformity_score",
    "color_score", "saturation_score", "luminance_score",
})

# **`style_similarity_score` is deliberately excluded**, and this is a boundary,
# not an oversight. `compute_style_similarity` writes it through a Core bulk
# `update(Image)` with no per-row load, so it cannot evaluate this predicate at
# all; letting it clear the bit would declare blur and aesthetic fresh when they
# are not. Keeping it in the clear universe instead would strand the bit
# permanently on any dataset that has ever run style similarity. It stays in the
# *set* universe (`utils.score_columns` — it is a measurement of those pixels),
# which leaves one corner written down rather than fixed: a row carrying only
# `style_similarity_score` is marked, then cleared by any later run that measured
# something. See `docs/dev/quality/scores-stale.md`.
_UNREFRESHABLE_SCORE_COLUMNS = frozenset({"style_similarity_score"})

# Every score column the quality job is able to refresh — the universe the
# `scores_stale` clear predicate covers. Anything a row carries that is *not*
# here can never be brought up to date by this job, so including it would make
# the bit un-clearable.
#
# Derived from the same source as the set site (`utils.record_in_place`), so the
# two ends of the bit are provably over one set. Derivation is the safe default
# because the failure directions are asymmetric: a hand-written list that misses
# an eleventh column **over-clears** — the badge vanishes while a score is stale,
# silently, which is the exact bug the feature exists to prevent — while a derived
# set that gains a column this job cannot refresh makes the bit **un-clearable**,
# a permanent badge that gets reported. `backend/tests/test_scores_stale.py` pins
# the resulting nine names, so an eleventh score column fails CI with a decision to
# make rather than either outcome. `_TECHNICAL_SCORE_COLUMNS` above stays an
# explicit literal: it names what one block writes, not a property of the schema.
_JOB_SCORE_COLUMNS = score_columns(Image) - _UNREFRESHABLE_SCORE_COLUMNS

# The model ids POST /quality/score can load. One definition so the job's
# auto-unload, the unload endpoint and the page's button cannot drift.
#
# Deliberately not a `kind`-based filter over `model_manager.list_models()`:
# `kind == "embed"` also matches `tag_embedder`, which scoring never loads and
# tag consolidation would then have to reload.
SCORING_MODEL_IDS = ("aesthetic", "aesthetic_v2_5", "dino", "nsfw")


def _decode_dino_layers(blob: bytes) -> np.ndarray:
    """Decode a per-layer DINOv2 blob into an (12, 768) float32 array (rows already L2-normalized)."""
    return np.frombuffer(blob, dtype=np.float16).reshape(_DINO_LAYERS, _DINO_DIM).astype(np.float32)


def _mean_layer_refs(ref_blobs: list[bytes]) -> np.ndarray:
    """Per-layer normalized mean reference embedding, shape (12, 768).

    Mirrors compute_style_similarity's normalize-then-dot pattern applied
    independently to each of the 12 layers: stack the references, mean over
    references per layer, then L2-normalize each layer's mean vector.
    """
    ref_stack = np.stack([_decode_dino_layers(b) for b in ref_blobs])  # (R, 12, 768)
    mean = ref_stack.mean(axis=0)  # (12, 768)
    norms = np.linalg.norm(mean, axis=1, keepdims=True)  # (12, 1)
    return mean / (norms + 1e-8)


class ScoreRequest(BaseModel):
    dataset_id: str
    subfolder: str | None = None
    image_ids: list[str] | None = None
    run_aesthetic: bool = True
    # Which producer writes `Image.aesthetic_score` (and the marker stored beside
    # it). A `Literal` rather than a plain `str`: an unknown value must be a 422,
    # never an unknown marker in the DB. The default keeps every existing client
    # byte-identical, and `body.model_dump()` carries it onto
    # `BackgroundJob.config` for free — job history is where a user later asks
    # which model made these numbers.
    aesthetic_model: Literal["laion", "v2_5"] = "laion"
    # Re-score only rows already scored by a *different* model. A scoping flag
    # rather than an id round-trip, so the selection stays atomic with the run
    # and composes with `subfolder` like every other scope. Deliberately excludes
    # never-scored rows: plain *Run scoring* already covers those, and two
    # buttons with two literal meanings beat one with a mode.
    only_mismatched: bool = False
    # Resume path: each check visits only rows missing its own output or marked
    # stale, so re-running a stopped job picks up where it left off — the rows
    # themselves are the checkpoint. A failed measurement is stored as NULL, so it
    # is retried too. Cannot be combined with `only_mismatched` (a 422).
    only_unscored: bool = False
    run_technical: bool = True
    run_watermark: bool = False
    run_embeddings: bool = False
    run_dino: bool = False
    run_dino_layers: bool = False
    run_nsfw: bool = False
    label: str | None = None


class DuplicateResolve(BaseModel):
    keep_ids: list[str]
    delete_ids: list[str]


class StyleSimilarityRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None  # scope scoring to these images only (None = whole dataset)
    reference_image_ids: list[str] = []
    reference_embeddings: list[str] = []  # base64-encoded float16 bytes (from embed-references)
    # "clip" | "dino" | "combined" | "dino_all_layers" | "combined_all_layers".
    embedding_type: str = "clip"
    # 1–12, honoured by both `dino` and `combined`; ignored by the `*_all_layers`
    # modes, which score every layer.
    #
    # **`None` is a sentinel, not "unspecified"**: it selects the post-layernorm
    # `dino_embedding` column, which is a *different vector* from
    # `dino_layer_embeddings`' layer 12 (`hidden_states[12]`, pre-layernorm).
    # Defaulting this to `DEFAULT_DINO_LAYER` would silently repoint every existing
    # API client — `backend/scripts/style_gate.py` among them — onto a column they
    # never asked for. The picker default lives in the frontend, where it belongs.
    dino_layer: int | None = None


@router.post("/score")
async def score_quality(body: ScoreRequest, db: AsyncSession = Depends(get_db)):
    return await start_score_job(db, body)


# How many images one check scores between commits. Small enough that a crash loses at
# most a few minutes of work and one chunk's results (layer embeddings are 18 KB each)
# are all the job ever holds; large enough that the per-chunk selects and the commit
# stay negligible beside the inference. Well under SQLite's bind limit (`utils.chunked`).
SCORE_CHUNK = 1000


@dataclass(frozen=True)
class _ScoreCheck:
    """One check of a scoring job: what it writes, and which rows still need it.

    `score_cols` feeds the `scores_stale` clear predicate (empty for the two
    embedding checks, which write no score column). `unscored` is the check's own
    half of the `only_unscored` filter — the job ORs `scores_stale` onto it.
    `passes` is how many scorer passes a chunk takes, for progress only.
    """
    key: str
    score_cols: frozenset[str]
    unscored: object
    writes_flags: bool
    passes: int = 1


def _score_checks(body: ScoreRequest) -> list[_ScoreCheck]:
    """The ticked checks, in the order the job runs them."""
    checks = []
    if body.run_aesthetic:
        checks.append(_ScoreCheck(
            "aesthetic", frozenset({"aesthetic_score"}), Image.aesthetic_score.is_(None), False,
        ))
    if body.run_technical:
        # Any of the six, not just `blur_score`, so a re-run fills a column added
        # after the row was first scored (as `luminance_score` was).
        checks.append(_ScoreCheck(
            "technical", _TECHNICAL_SCORE_COLUMNS,
            or_(*(getattr(Image, c).is_(None) for c in sorted(_TECHNICAL_SCORE_COLUMNS))),
            True,
        ))
    if body.run_watermark:
        checks.append(_ScoreCheck(
            "watermark", frozenset({"watermark_score"}), Image.watermark_score.is_(None), True,
        ))
    if body.run_embeddings:
        checks.append(_ScoreCheck(
            "embeddings", frozenset(), Image.clip_embedding.is_(None), False,
        ))
    if body.run_dino:
        unscored = Image.dino_embedding.is_(None)
        if body.run_dino_layers:
            unscored = or_(unscored, Image.dino_layer_embeddings.is_(None))
        checks.append(_ScoreCheck(
            "dino", frozenset(), unscored, False, passes=2 if body.run_dino_layers else 1,
        ))
    if body.run_nsfw:
        checks.append(_ScoreCheck(
            "nsfw", frozenset({"nsfw_score"}), Image.nsfw_score.is_(None), True,
        ))
    return checks


def _check_filter(check: _ScoreCheck, stale_ids) -> object:
    """The rows a check visits under `only_unscored`: those missing its output
    **or stale when the job started** (`stale_ids`, a snapshot).

    The stale half is not a convenience, and it is a snapshot rather than a live
    `scores_stale` test on purpose. Every stale row is then visited by every
    check, which is what lets the clear predicate count the earlier checks'
    columns as refreshed (see `_write_score_chunk`). A live test would drop a row
    from the later checks once an earlier one cleared its bit, and its CLIP and
    DINOv2 embeddings would keep describing the old pixels with nothing left to
    say so.
    """
    stale_ids = list(stale_ids)
    if not stale_ids:
        return check.unscored
    return or_(check.unscored, Image.id.in_(stale_ids))


async def start_score_job(
    db: AsyncSession,
    body: ScoreRequest,
    *,
    run_id: str | None = None,
    run_stage: str | None = None,
    label_prefix: str = "",
    extra_config: dict | None = None,
) -> dict:
    """Queue a quality-scoring job; the `/score` route, an automated run and a Retry
    (`services/job_retry.py`, which passes `extra_config={"retry_of": ...}`) all start here.

    Answers `{"job_id": None, ...}` when nothing is in scope. **`image_ids: []` widens
    to the whole dataset** (`if body.image_ids:`), so a caller from code must not pass
    an empty list — the automated run skips the stage instead.

    Only ids are read here. The scope is frozen now, so a selection stays atomic with
    the run; the job reads each chunk's paths and state itself, just before it needs
    them.
    """
    if body.only_unscored and body.only_mismatched:
        # They contradict on aesthetic: one wants rows with no score, the other
        # rows scored by another model.
        raise HTTPException(422, "only_unscored and only_mismatched cannot be combined.")

    checks = _score_checks(body)

    query = select(Image.id).where(Image.dataset_id == body.dataset_id)
    # With `image_ids`, the id filter is applied per chunk below.
    if not body.image_ids and body.subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(body.subfolder))
    if body.only_mismatched:
        # `score non-NULL <=> marker non-NULL` (migration a5e1b7c3d9f0), so the
        # score test alone is what excludes never-scored rows — the marker test
        # then leaves exactly the rows another model measured.
        query = query.where(
            Image.aesthetic_score.isnot(None),
            Image.aesthetic_model.isnot(body.aesthetic_model),
        )
    if body.only_unscored and checks:
        # The union of the checks' own filters, so the count in the label and the
        # nothing-to-do answer describe rows some check will actually visit.
        query = query.where(or_(
            Image.scores_stale.is_(True), *(c.unscored for c in checks)
        ))
    if body.image_ids:
        # A run can pass up to 100k ids, past SQLite's bind-parameter limit.
        ids: list[str] = []
        for chunk in chunked(list(dict.fromkeys(body.image_ids))):
            ids.extend((await db.execute(query.where(Image.id.in_(chunk)))).scalars().all())
    else:
        ids = list((await db.execute(query)).scalars().all())

    if not ids:
        return {
            "job_id": None,
            "message": "Nothing left to score" if body.only_unscored else "No images found",
        }

    aesthetic_check = (
        "aesthetic (V2.5)" if body.aesthetic_model == "v2_5" else "aesthetic"
    )
    check_names = [c for c, flag in [
        ("technical", body.run_technical),
        (aesthetic_check, body.run_aesthetic),
        ("watermark", body.run_watermark),
        ("embeddings", body.run_embeddings),
        ("DINOv2", body.run_dino),
        ("NSFW", body.run_nsfw),
    ] if flag]
    auto_label = f"Quality: {', '.join(check_names) or 'none'} — {len(ids)} image{'s' if len(ids) != 1 else ''}"
    job = BackgroundJob(
        job_type="quality_score",
        label=run_job_label(label_prefix, body.label or auto_label),
        dataset_id=body.dataset_id,
        total_items=len(ids),
        config=run_job_config({**body.model_dump(), **(extra_config or {})}, run_id, run_stage),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.ml.aesthetic_scorer import (
            extract_clip_embeddings_batch,
            score_images_batch,
            score_images_watermark,
        )
        from backend.ml.technical_scorer import score_images_technical
        from backend.services.threshold_service import get_thresholds
        from backend.workers.progress import broadcaster

        async with AsyncSessionLocal() as ts_session:
            thresholds = await get_thresholds(ts_session)

        # Collected beside each loader call rather than re-derived from `body`,
        # the same discipline `aesthetic_marker` below describes: what gets
        # unloaded is provably what got loaded.
        loaded_ids: set[str] = set()
        # Bound up front because the `finally` drops them; see the comment there.
        entry = handle = dino_entry = nsfw_entry = None

        try:
            loop = asyncio.get_running_loop()

            # Captured once, next to the choice of loader below, so the marker
            # written to the DB and the model actually run are provably the same
            # decision rather than two reads of `body` that could drift.
            aesthetic_marker: str | None = body.aesthetic_model if body.run_aesthetic else None

            # The rows stale when the job starts. Only these can have their bit
            # cleared by this job: a row that turns stale mid-job was edited while
            # the job ran, so a score measured before the edit may describe pixels
            # that are gone. Under `only_unscored` the snapshot also decides which
            # rows every check visits (`_check_filter`). Memory is one id per stale
            # row.
            stale_at_start: set[str] = set()
            async with AsyncSessionLocal() as s:
                for chunk in (chunked(ids) if checks else ()):
                    stale_at_start.update((await s.execute(
                        select(Image.id).where(Image.id.in_(chunk), Image.scores_stale.is_(True))
                    )).scalars())

            def _chunk_filter(check: _ScoreCheck, chunk_ids):
                if not body.only_unscored:
                    return None
                return _check_filter(check, (i for i in chunk_ids if i in stale_at_start))

            # Each check's row count, so one monotone `percent` spans the whole job
            # (the `video_extract` precedent) while `done`/`total` count images
            # within the current check. Without `only_unscored` every check visits
            # the whole scope; with it, a COUNT per check over the frozen ids.
            totals: list[int] = []
            for check in checks:
                if not body.only_unscored:
                    totals.append(len(ids))
                    continue
                n = 0
                async with AsyncSessionLocal() as s:
                    for chunk in chunked(ids):
                        n += (await s.execute(
                            select(func.count()).select_from(Image)
                            .where(Image.id.in_(chunk), _chunk_filter(check, chunk))
                        )).scalar() or 0
                totals.append(n)
            units_total = sum(t * c.passes for t, c in zip(totals, checks)) or 1
            units_before = 0  # finished checks' units
            # A concurrent edit can make a check visit more rows than it counted;
            # the bar still never moves backwards.
            last_percent = [0.0]

            # Score columns of every check that has finished all its chunks, and
            # the stale-at-start rows some check should have written but did not
            # (a chunk that came back short). Together they make the clear
            # predicate's `refreshed` exact per row: a row in `missed` is never
            # cleared by this job.
            done_cols: set[str] = set()
            missed: set[str] = set()

            for step, (check, check_total) in enumerate(zip(checks, totals), start=1):

                # Load once per check, then one lease for the whole check, taken
                # with no `await` after the load — never one for the whole job,
                # which would stop `_evict_lru` freeing this model while `dino`
                # loads (GPT-032). Every lease is gone before the `finally` unloads.
                if check.key == "aesthetic":
                    if aesthetic_marker == "v2_5":
                        entry = await model_manager.load_aesthetic_v2_5(
                            job_id=job_id, loop=loop, dataset_id=body.dataset_id
                        )
                        loaded_ids.add("aesthetic_v2_5")
                        # The plain entry shape carries the processor alongside the model.
                        handle = entry
                    else:
                        entry = await model_manager.load_aesthetic(
                            job_id=job_id, loop=loop, dataset_id=body.dataset_id
                        )
                        loaded_ids.add("aesthetic")
                        handle = entry.model  # the three-tenant {"clip","mlp","preprocess"} dict
                    lease = model_manager.lease(entry)
                elif check.key in ("watermark", "embeddings"):
                    entry = await model_manager.load_aesthetic(
                        job_id=job_id, loop=loop, dataset_id=body.dataset_id
                    )
                    loaded_ids.add("aesthetic")
                    lease = model_manager.lease(entry)
                elif check.key == "dino":
                    from backend.ml.dino_scorer import (
                        extract_embeddings_dino,
                        extract_layer_embeddings_dino,
                    )
                    dino_entry = await model_manager.load_dino(
                        job_id=job_id, loop=loop, dataset_id=body.dataset_id
                    )
                    loaded_ids.add("dino")
                    lease = model_manager.lease(dino_entry)
                elif check.key == "nsfw":
                    from backend.ml.nsfw_scorer import score_images_nsfw_batch
                    nsfw_entry = await model_manager.load_nsfw(
                        job_id=job_id, loop=loop, dataset_id=body.dataset_id
                    )
                    loaded_ids.add("nsfw")
                    lease = model_manager.lease(nsfw_entry)
                else:  # technical: no model
                    lease = contextlib.nullcontext()

                rows_done = 0  # this check's rows in finished chunks
                with lease:
                    for chunk_ids in chunked(ids, SCORE_CHUNK):
                        # Paths are read per chunk, so a row deleted since the
                        # request simply drops out, and a rename is followed.
                        async with AsyncSessionLocal() as s:
                            q = (
                                select(Image.id, Image.file_path)
                                .where(Image.id.in_(chunk_ids))
                                .order_by(Image.id)
                            )
                            filt = _chunk_filter(check, chunk_ids)
                            if filt is not None:
                                q = q.where(filt)
                            todo = (await s.execute(q)).all()
                        if not todo:
                            continue
                        c_ids = [r.id for r in todo]
                        c_paths = [r.file_path for r in todo]

                        def _progress(pass_no: int, message: str):
                            base_units = units_before + rows_done * check.passes + pass_no * len(todo)

                            async def emit(n: int) -> None:
                                done = min(rows_done + (pass_no * len(todo) + n) // check.passes, check_total)
                                last_percent[0] = max(
                                    last_percent[0],
                                    round(min((base_units + n) / units_total, 1.0) * 100, 1),
                                )
                                await broadcaster.emit(job_id, {
                                    "type": "progress", "job_id": job_id,
                                    "job_type": "quality_score", "status": "running",
                                    "done": done, "total": check_total,
                                    "percent": last_percent[0],
                                    "message": (
                                        f"{message} {done:,}/{check_total:,}"
                                        + (f" · step {step} of {len(checks)}" if len(checks) > 1 else "")
                                    ),
                                })
                            return emit

                        # Each scorer stops early on a cancel request and returns a
                        # shorter list: every value below is guarded by its length,
                        # so the finished part of the chunk is still saved.
                        values: list[dict] = []
                        flag_values: list[dict] = []
                        if check.key == "aesthetic":
                            scores = await score_images_batch(
                                c_paths, handle, job_id=job_id, model=aesthetic_marker,
                                progress=_progress(0, "Aesthetic scoring"),
                            )
                            # Never written without a score, never survives one. A
                            # marker beside a NULL score claims a model looked and
                            # produced nothing; a stale marker on an overwritten NULL
                            # puts a phantom row in the per-model breakdown. Both break
                            # the migration's invariant (score non-NULL <=> marker
                            # non-NULL).
                            values = [
                                {"aesthetic_score": sc,
                                 "aesthetic_model": aesthetic_marker if sc is not None else None}
                                for sc in scores
                            ]
                        elif check.key == "technical":
                            results = await score_images_technical(
                                c_ids, c_paths, job_id=job_id,
                                blur_threshold=thresholds.blur_threshold,
                                noise_threshold=thresholds.noise_threshold,
                                uniformity_threshold=thresholds.uniformity_threshold,
                                progress=_progress(0, "Technical scoring"),
                            )
                            values = [{c: t.get(c) for c in _TECHNICAL_SCORE_COLUMNS} for t in results]
                            flag_values = [
                                {"is_blurry": t.get("is_blurry", False),
                                 "is_noisy": t.get("is_noisy", False),
                                 "is_uniform": t.get("is_uniform", False)}
                                for t in results
                            ]
                        elif check.key == "watermark":
                            results = await score_images_watermark(
                                c_paths, entry.model, job_id=job_id,
                                watermark_threshold=thresholds.watermark_threshold,
                                progress=_progress(0, "Watermark check"),
                            )
                            values = [{"watermark_score": w.get("watermark_score")} for w in results]
                            flag_values = [{"has_watermark": w.get("has_watermark", False)} for w in results]
                        elif check.key == "embeddings":
                            embs = await extract_clip_embeddings_batch(
                                c_paths, entry.model, job_id=job_id,
                                progress=_progress(0, "CLIP embeddings"),
                            )
                            values = [{"clip_embedding": e} for e in embs]
                        elif check.key == "dino":
                            # One label for both passes: the count is the check's
                            # images, averaged over the two passes, so naming one
                            # pass would misstate how far that pass has got.
                            dino_label = (
                                "DINOv2 embeddings + layers" if body.run_dino_layers
                                else "DINOv2 embeddings"
                            )
                            finals = await extract_embeddings_dino(
                                c_paths, dino_entry, job_id=job_id,
                                progress=_progress(0, dino_label),
                            )
                            values = [{"dino_embedding": e} for e in finals]
                            if body.run_dino_layers and not job_queue.cancel_requested(job_id):
                                layers = await extract_layer_embeddings_dino(
                                    c_paths, dino_entry, job_id=job_id,
                                    progress=_progress(1, dino_label),
                                )
                                for v, e in zip(values, layers):
                                    v["dino_layer_embeddings"] = e
                        elif check.key == "nsfw":
                            results = await score_images_nsfw_batch(
                                c_paths, nsfw_entry.model,
                                threshold=thresholds.nsfw_threshold, job_id=job_id,
                                progress=_progress(0, "NSFW scoring"),
                            )
                            values = [{"nsfw_score": n.get("nsfw_score")} for n in results]
                            flag_values = [{"is_nsfw": n.get("is_nsfw", False)} for n in results]

                        written_ids = c_ids[:len(values)]
                        # Real scorers only come back short on a cancel, and the job
                        # raises right after this chunk, but the clear predicate does
                        # not rest on that.
                        missed.update(i for i in c_ids[len(values):] if i in stale_at_start)
                        if written_ids:
                            await _write_score_chunk(
                                AsyncSessionLocal, check, written_ids, values, flag_values,
                                done_cols, stale_at_start - missed,
                            )
                        rows_done += len(todo)

                        # Persist completed work before honoring the cancellation.
                        job_queue.raise_if_cancelled(job_id)

                done_cols |= check.score_cols
                units_before += check_total * check.passes
                # A check with nothing to do never reached the per-chunk check.
                job_queue.raise_if_cancelled(job_id)

            # Detect duplicates after scoring
            if body.run_technical:
                await _flag_duplicates(job_id, body.dataset_id, int(thresholds.duplicate_threshold))
        finally:
            # A `finally` rather than a tail statement, for three reasons: a run
            # cancelled through `raise_if_cancelled` should free VRAM too; a scorer
            # that raises should not strand 3.5 GB; and every chunk's `commit()` is
            # already past, so this is the PM-013 post-commit epilogue — it logs and
            # can never change the job's outcome. Awaiting here is safe because
            # cancellation is cooperative (`raise_if_cancelled` raises by hand). The
            # one exception is shutdown: `JobQueue.stop()` cancels the worker task
            # once if the job outlives its grace. Landing earlier, that cancel only
            # brings us here, where the awaits run normally; landing on one of
            # them, it cuts the remaining unloads short — which costs nothing in a
            # process that is exiting.
            #
            # Scoped to `loaded_ids`, never `evict_all()`: a Florence-2 or SAM2
            # someone loaded for other work is not this job's to evict.
            if thresholds.auto_unload_after_scoring and loaded_ids:
                # Drop this frame's own references *first*. `unload` moves the
                # weights to CPU and then flushes the allocator, but the flush
                # only returns what nothing still holds — and these four names
                # are live locals right here in the `finally`, one of them
                # (`handle`) a second reference to `aesthetic`'s three-tenant
                # dict. Left bound, they kept ~3.5 GB resident past
                # `empty_cache()`; the frame then died with no second flush to
                # follow, so the process held the VRAM until it exited.
                # `_release_to_cpu` alone would fix the residency, but the
                # allocator still cannot hand back a live buffer, so both ends
                # are needed.
                entry = handle = dino_entry = nsfw_entry = None
                # Caught per id: this job's own leases are all released by now,
                # but a concurrent holder (an embed-references request, another
                # job) can still lease one of these, and a refusal for that id
                # must not strand the others in VRAM.
                for mid in sorted(loaded_ids):
                    try:
                        await model_manager.unload(mid)
                    except ModelInUseError:
                        logger.info(
                            "Auto-unload after scoring: %s is leased elsewhere; "
                            "leaving it resident (job %s)", mid, job_id,
                        )
                    except Exception:
                        logger.exception(
                            "Auto-unload of %s after scoring failed for job %s", mid, job_id
                        )

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": len(ids)}


async def _write_score_chunk(
    session_factory,
    check: _ScoreCheck,
    written_ids: list[str],
    values: list[dict],
    flag_values: list[dict],
    done_cols: set[str],
    clearable: set[str],
) -> None:
    """Write one chunk of one check's results and commit it.

    The row state the write depends on — `scores_stale`, the job's score columns
    and `quality_flags` — is read here, in the writing session, right before the
    write, so a concurrent writer cannot lose an update to a stale read of that
    state. That does not make the *scores* current: they were measured before
    the write, and a pixel edit landing in between is caught only by the bit
    (see `clearable`). A row deleted since is skipped.

    `clearable` is the rows this job may clear: stale when it started, and
    written by every check so far.
    """
    state_cols = [Image.id, Image.scores_stale]
    state_cols += [getattr(Image, c) for c in sorted(_JOB_SCORE_COLUMNS)]
    if check.writes_flags:
        state_cols.append(Image.quality_flags)
    async with session_factory() as session:
        state = {
            r.id: r for r in (await session.execute(
                select(*state_cols).where(Image.id.in_(written_ids))
            )).all()
        }
        refreshed = done_cols | check.score_cols
        updates: list[dict] = []
        cleared: list[str] = []
        for i, img_id in enumerate(written_ids):
            row = state.get(img_id)
            if row is None:
                continue
            upd = {"id": img_id, **values[i]}
            if check.writes_flags:
                # Copy, then reassign — never mutate the loaded dict.
                flags = dict(row.quality_flags or {})
                flags.update(flag_values[i])
                upd["quality_flags"] = flags
            updates.append(upd)

            # The one place `scores_stale` is cleared. Set-covering over what this
            # job has *written* to the row: this check's columns plus those of
            # every check that already finished (`done_cols`). That union is
            # exact because every earlier check wrote this row too — a full run
            # visits every row, under `only_unscored` the stale-at-start snapshot
            # passes every check's filter, and a row some check missed is not in
            # `clearable`. A row that turned stale mid-job is not either: it was
            # edited while the job ran. So a re-score with the same checks
            # that produced the original numbers clears the bit, a watermark-only
            # pass never claims a stale blur score is fresh, and a row the run
            # never reached (a cancel truncates the chunk) is never in here.
            #
            # The state was read before this chunk's write, which is right: a
            # column this check writes is in `refreshed` whatever it held, and a
            # column it does not write is untouched by the write.
            #
            # `quality_flags` needs no separate tracking: each flag is written in
            # the same values as the score it derives from. `is_duplicate`
            # derives from `phash`, which every in-place path re-derives, so
            # duplicate flags were never stale.
            #
            # A run that measured *nothing* clears nothing: the two embedding
            # checks write no score column, and `stale_left` would otherwise be
            # empty for any row whose job-score columns are all NULL — clearing
            # the bit having taken no measurement at all. `ScoreRequest` has no
            # `run_duplicates`; duplicate flagging rides on `run_technical`,
            # which does write scores.
            if check.score_cols and row.scores_stale and img_id in clearable:
                stale_left = {
                    c for c in _JOB_SCORE_COLUMNS if getattr(row, c) is not None
                } - refreshed
                if not stale_left:
                    cleared.append(img_id)
        if updates:
            # ORM bulk UPDATE by primary key; `updated_at`'s `onupdate` still fires,
            # so the stats cache sees the change.
            await session.execute(update(Image), updates)
        if cleared:
            await session.execute(
                update(Image).where(Image.id.in_(cleared)).values(scores_stale=False)
            )
        await session.commit()


# ---------------------------------------------------------------------------
# Scoring model lifecycle
#
# Both endpoints live here rather than in `routers/models.py`, following the
# captioning precedent (`DELETE /captioning/model/{id}/unload`): the unload sits
# beside the job whose models it frees. `models.py` and its callerless
# `/models/unload-all` are untouched — that one evicts *everything*, which is
# exactly what a scoping-aware unload must not do.
# ---------------------------------------------------------------------------

@router.get("/models")
async def list_scoring_models():
    """The four models scoring can load, each with a `loaded` flag and `vram_mb`.

    Backs the page's *Unload models* button, which needs both halves: whether to
    render at all, and how much it will free.
    """
    return [m for m in model_manager.list_models() if m["id"] in SCORING_MODEL_IDS]


@router.post("/models/unload")
async def unload_scoring_models(db: AsyncSession = Depends(get_db)):
    """Free every scoring model currently resident, and nothing else.

    The resident set is read *before* the unloads so the response describes what
    was actually freed. `model_manager.unload` is a no-op for an unregistered id
    and takes the per-model lock, so calling it for all four is safe; no id comes
    from the client, so there is nothing to validate.

    **Refused while a scoring run is in flight.** The scoring job leases each model
    only for the stage that uses it (`ModelManager.lease`), so between stages a
    model it loaded — and will auto-unload, or reload in a later stage — is
    unleased; the job-level 409 keeps a run's models out of reach for its whole
    life rather than only mid-stage. The page's own `isRunning` is not that guard:
    it knows only about a job *it* started, and goes stale on a reload or in a
    second pane.

    **A leased model is skipped, not an error.** A concurrent embed-references
    request or another job's lease makes `unload` raise `ModelInUseError`; that id
    stays resident, is left out of `unloaded` and `freed_mb`, and is reported under
    `in_use`, while every other id still unloads.

    `quality_score` is the exact scope, not a blunt any-job check: it is the only
    job type that loads anything in `SCORING_MODEL_IDS`. Style similarity reads
    stored embeddings without loading `dino`, and detection loads sam2/sam3, which
    this endpoint never touches.

    Not `dataset_busy.ensure_not_busy` — that flag is dataset-scoped and the scoring
    job never takes it, while VRAM residency is a property of the whole process.
    """
    running = (await db.execute(
        select(BackgroundJob.id)
        .where(
            BackgroundJob.job_type == "quality_score",
            BackgroundJob.status.in_(("pending", "running")),
        )
        .limit(1)
    )).scalar_one_or_none()
    if running:
        raise HTTPException(
            409, "A scoring run is in progress. Try again when it finishes."
        )

    resident = [
        m for m in model_manager.list_models()
        if m["id"] in SCORING_MODEL_IDS and m["loaded"]
    ]
    in_use: list[str] = []
    for mid in SCORING_MODEL_IDS:
        try:
            await model_manager.unload(mid)
        except ModelInUseError:
            in_use.append(mid)
    freed = [m for m in resident if m["id"] not in in_use]
    return {
        "unloaded": [m["id"] for m in freed],
        "freed_mb": sum(int(m["vram_mb"]) for m in freed),
        "in_use": in_use,
    }


async def _flag_duplicates(job_id: str, dataset_id: str, duplicate_threshold: int) -> None:
    """Re-derive the whole dataset's duplicate grouping and store it.

    Module level rather than a closure over the scoring job: it already takes
    exactly the three values it needs, and this is the dataset's authoritative
    recomputation — worth being callable and readable on its own.
    """
    from backend.database import AsyncSessionLocal
    from backend.ml.technical_scorer import find_duplicates_sync

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Image.id, Image.phash).where(
                Image.dataset_id == dataset_id,
                Image.phash.isnot(None),
            )
        )
        phashes = [(r.id, r.phash) for r in result.all()]

    # The dedup scan (either dispatch path) is uninterruptible once started;
    # skip it entirely if cancelled.
    job_queue.raise_if_cancelled(job_id)
    fn = functools.partial(find_duplicates_sync, phashes, duplicate_threshold)
    groups = await asyncio.get_running_loop().run_in_executor(None, fn)

    # Map each duplicate image id to the group root it should point at.
    dup_of: dict[str, str] = {}
    for group in groups:
        keep = group[0]
        for dup_id in group[1:]:
            dup_of[dup_id] = keep

    # No `if not dup_of: return`. Zero groups is not "nothing to do" — it is
    # precisely the case that has to clear every stale flag in the dataset, and
    # it is what makes a Technical re-scan the repair path for drift written by
    # an older build or by an earlier run at a looser `duplicate_threshold`.
    # `apply_duplicate_groups` owns both directions; see PM-022.
    async with AsyncSessionLocal() as session:
        await apply_duplicate_groups(session, dataset_id, dup_of)
        await session.commit()


@router.get("/aesthetic-coverage/{dataset_id}")
async def aesthetic_coverage(
    dataset_id: str,
    subfolder: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """How many images are scored, by which model, within the Score page's scope.

    A dedicated endpoint rather than a field on `get_dataset_stats`: this needs
    the page's own subfolder scope and a refetch every time a scoring job
    finishes, and the Score page otherwise makes three light queries — pulling
    the whole stats aggregation onto it for a count would be the expensive way
    round. The Stats page gets the same breakdown from its existing query
    instead, as flat `aesthetic_{marker}` keys.

    `by_model` sums to `scored`, which is the invariant migration a5e1b7c3d9f0's
    backfill buys: every non-NULL score carries a non-NULL marker, so there is no
    "scored but unknown" bucket to account for.
    """
    scope = [Image.dataset_id == dataset_id]
    if subfolder is not None:
        scope.append(Image.subfolder == normalize_subfolder(subfolder))

    total, scored = (await db.execute(
        select(func.count(Image.id), func.count(Image.aesthetic_score)).where(*scope)
    )).one()

    rows = (await db.execute(
        select(Image.aesthetic_model, func.count(Image.id))
        .where(*scope, Image.aesthetic_model.isnot(None))
        .group_by(Image.aesthetic_model)
    )).all()

    return {
        "scored": scored,
        "unscored": total - scored,
        "by_model": {marker: count for marker, count in rows},
    }


@router.get("/embedding-coverage/{dataset_id}")
async def embedding_coverage(
    dataset_id: str,
    subfolder: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """How many images carry each embedding a style run needs, within the page's scope.

    Style similarity is the one workflow whose prerequisite is another run: every
    mode 400s when the column it reads is empty, and until now the only way to find
    that out was to press the button. This is what lets the Score page say so
    first — and disable *Score similarity* when the answer is zero, which is a
    guaranteed 400.

    A dedicated endpoint for the same reason as `aesthetic_coverage` above: it needs
    the page's own subfolder scope and a refetch every time a scoring job finishes,
    and pulling the whole `get_dataset_stats` aggregation onto the Score page for
    three counts would be the expensive way round.

    Explicitly **not** a field on `GET /style-similarity/{id}` — that endpoint is on
    the gallery render path via `ImageCard`, and this is a Score-page question.

    `func.count` over a `deferred=True` LargeBinary is a column expression evaluated
    in SQLite; no blob is loaded, so counting `dino_layer_embeddings` does not read
    18 KB per row.
    """
    scope = [Image.dataset_id == dataset_id]
    if subfolder is not None:
        scope.append(Image.subfolder == normalize_subfolder(subfolder))

    total, clip, dino, dino_layers = (await db.execute(
        select(
            func.count(Image.id),
            func.count(Image.clip_embedding),
            func.count(Image.dino_embedding),
            func.count(Image.dino_layer_embeddings),
        ).where(*scope)
    )).one()

    return {
        "total": total,
        "clip": clip,
        "dino": dino,
        "dino_layers": dino_layers,
    }


@router.post("/embed-references")
async def embed_references(files: list[UploadFile] = File(...)):
    """Compute CLIP embeddings for uploaded reference images.
    Returns base64-encoded float16 bytes that can be passed to /style-similarity."""
    from backend.ml.aesthetic_scorer import extract_clip_embedding_from_bytes_sync

    entry = await model_manager.load_aesthetic()
    embeddings = []
    # Held for every file: a scoring job's auto-unload (or the Unload button)
    # between two files would otherwise `del` the weights under the next one.
    with model_manager.lease(entry):
        for f in files:
            img_bytes = await f.read()
            fn = functools.partial(extract_clip_embedding_from_bytes_sync, img_bytes, entry.model)
            emb_bytes = await _device.run_on_gpu(fn)
            embeddings.append(base64.b64encode(emb_bytes).decode())
    return {"embeddings": embeddings}


async def _record_style_run(body: StyleSimilarityRequest, db: AsyncSession, result: dict) -> None:
    """Upsert the dataset's style-run descriptor from a run that has already committed.

    Select-then-write rather than an `on_conflict` construct (there is none
    anywhere in `backend/`), and swallow-and-log: the scoring itself is already
    durable by the time this runs, so per `CLAUDE.md`'s post-commit epilogue rule a
    failure here costs the run's *description*, not the run.

    A **scoped** run (`image_ids` set — the SelectionToolbar path) overwrites the
    descriptor anyway and records `scoped_image_count`. It is the most recent run
    and its references do describe the most recently written values; the count is
    what lets the UI qualify the rest of the dataset instead of hiding the feature.
    """
    try:
        run = (await db.execute(
            select(StyleSimilarityRun).where(StyleSimilarityRun.dataset_id == body.dataset_id)
        )).scalar_one_or_none()
        if run is None:
            run = StyleSimilarityRun(dataset_id=body.dataset_id)
            db.add(run)
        run.embedding_type = body.embedding_type
        # From `result`, not from `body`. `dino_layer` here means "the layer whose
        # value is in `style_similarity_score`", which for an `*_all_layers` run is
        # `DEFAULT_DINO_LAYER` and not the `None` the request carried; reading the
        # body would record a different fact from the one the run computed. Same
        # argument as `aesthetic_marker` in `score_quality`. NULL weights on a
        # non-blending mode are the correct value, and `embedding_type` in the same
        # row is what tells that apart from a row predating the columns.
        run.dino_layer = result.get("dino_layer")
        run.clip_weight = result.get("clip_weight")
        run.dino_weight = result.get("dino_weight")
        run.reference_image_ids = list(body.reference_image_ids[:REFERENCE_IDS_STORED_MAX])
        run.reference_count = len(body.reference_image_ids)
        run.external_reference_count = len(body.reference_embeddings)
        run.scored_count = int(result.get("updated") or 0)
        run.skipped_count = int(result.get("skipped") or 0)
        run.scoped_image_count = len(body.image_ids) if body.image_ids else None
        run.updated_at = datetime.utcnow()
        await db.commit()
    except Exception:  # noqa: BLE001 — a lost descriptor must never fail a committed run
        logger.exception("Failed to record style-similarity run descriptor for dataset %s", body.dataset_id)
        await db.rollback()


@router.post("/style-similarity")
async def compute_style_similarity(
    body: StyleSimilarityRequest,
    db: AsyncSession = Depends(get_db),
):
    """Score every candidate image against the reference set, then record what did it.

    A thin wrapper on purpose. `_score_style_similarity` has six success returns
    (one per branch), so writing the descriptor inline would be six drift-prone
    call sites; doing it here makes the write **success-only for free**, since
    every failure path in that function raises `HTTPException` and so never reaches
    this line. That matters: a run that raised must not overwrite a descriptor
    that still correctly describes the values in the column.
    """
    result = await _score_style_similarity(body, db)
    await _record_style_run(body, db, result)
    return result


async def _select_by_ids(db: AsyncSession, cols: tuple, ids) -> list:
    """Rows for a client-supplied id list, chunked under SQLite's bind limit (deduped)."""
    rows: list = []
    for chunk in chunked(list(dict.fromkeys(ids))):
        rows.extend((await db.execute(select(*cols).where(Image.id.in_(chunk)))).all())
    return rows


async def _score_style_similarity(body: StyleSimilarityRequest, db: AsyncSession) -> dict:
    from backend.ml.similarity_scorer import compute_style_similarity as _cosine_sim
    from backend.ml.similarity_scorer import (
        blend_scores,
        compute_combined_similarity,
        slice_layer_embedding,
    )

    loop = asyncio.get_running_loop()

    # Chunked under SQLite's bind limit; an empty list means the whole dataset.
    id_filters = (
        [(Image.id.in_(c),) for c in chunked(sorted(set(body.image_ids)))]
        if body.image_ids else [()]
    )
    total_images = 0
    for id_filter in id_filters:
        total_count_result = await db.execute(
            select(func.count(Image.id)).where(Image.dataset_id == body.dataset_id, *id_filter)
        )
        total_images += total_count_result.scalar() or 0

    async def _score_paginated(select_cols, extra_filters, score_chunk, *, defer_write: bool = False) -> int:
        """Keyset-paginate the candidate set and score each page off the event loop.

        Avoids loading every candidate's blob into one result set (~1.8 GB at 100k
        images for the ~18 KB per-layer blob). `score_chunk(rows) -> list[dict]` runs
        in an executor; `rows` are SQLAlchemy Rows whose first column is `Image.id`.
        Returns the number of rows updated. Used by **every** branch (M9 stage 5): a
        branch that selects its candidates whole is the bug this exists to prevent.

        Paging moves a score by at most one step in its 4th decimal. Each candidate is
        scored against the reference mean on its own, but a float32 matrix product's
        last bits depend on the batch's shape and row order, so a value sitting on a
        rounding boundary can flip. That was already true before paging: the old
        whole-set SELECT had no ORDER BY, so its order was SQLite's choice. A hand
        check on 804 real images (16 runs, 8,576 scores) found 1 such flip at this
        page size and 7 at a page of 37.

        The commit comes once, at the end, so a run that raises partway leaves the
        column as it was — which `compute_style_similarity`'s success-only descriptor
        write relies on. **When the UPDATEs run decides how long the write lock is
        held.** SQLite's first UPDATE takes the database's one write lock until the
        commit, and other writers (a scoring job's chunk commit, a caption save) wait
        the driver's default 5 s and then fail with "database is locked". So:

        - `defer_write=True` (the four one-score branches) keeps only `(id, score)`
          per row while scanning, about 100 bytes, and writes them all after the
          last page. The lock is held for the writes alone, as it was when these
          branches selected their candidates whole.
        - `defer_write=False` (the two `*_all_layers` branches) writes each page as it
          goes, because each row carries a 12-entry `dino_layer_scores` dict that is
          too big to hold for the whole dataset. Those two hold the lock for the whole
          scan, and always have.
        """
        total_updated = 0
        pending_ids: list[str] = []
        pending_scores: list[float] = []
        for id_filter in id_filters:
            last_id = ""
            while True:
                chunk_rows = (await db.execute(
                    select(*select_cols)
                    .where(
                        Image.dataset_id == body.dataset_id,
                        *extra_filters,
                        *id_filter,
                        Image.id > last_id,
                    )
                    .order_by(Image.id)
                    .limit(_STYLE_PAGE_SIZE)
                )).all()
                if not chunk_rows:
                    break
                updates = await loop.run_in_executor(None, score_chunk, chunk_rows)
                if defer_write:
                    for u in updates:
                        pending_ids.append(u["id"])
                        pending_scores.append(u["style_similarity_score"])
                elif updates:
                    await db.execute(update(Image), updates)
                    total_updated += len(updates)
                last_id = chunk_rows[-1][0]
        for start in range(0, len(pending_ids), _STYLE_PAGE_SIZE):
            await db.execute(update(Image), [
                {"id": i, "style_similarity_score": s}
                for i, s in zip(
                    pending_ids[start:start + _STYLE_PAGE_SIZE],
                    pending_scores[start:start + _STYLE_PAGE_SIZE],
                )
            ])
        total_updated += len(pending_ids)
        if total_updated:
            await db.commit()
        return total_updated

    def _cosine_chunk(ref_embs: list[bytes], layer: int | None = None):
        """A `score_chunk` for the one-vector modes: rows are `(Image.id, blob)`, and
        `layer` slices that numbered layer out of a per-layer blob first."""
        def score(chunk_rows) -> list[dict]:
            blobs = [r[1] for r in chunk_rows]
            if layer is not None:
                blobs = [slice_layer_embedding(b, layer) for b in blobs]
            scores = _cosine_sim(ref_embs, blobs)
            return [{"id": r[0], "style_similarity_score": s} for r, s in zip(chunk_rows, scores)]
        return score

    # --- CLIP branch ---
    if body.embedding_type == "clip":
        col = Image.clip_embedding
        ref_embs: list[bytes] = []
        if body.reference_image_ids:
            ref_rows_raw = await _select_by_ids(db, (Image.id, col), body.reference_image_ids)
            ref_embs.extend(r[1] for r in ref_rows_raw if r[1] is not None)
        for b64 in body.reference_embeddings:
            ref_embs.append(base64.b64decode(b64))
        if not ref_embs:
            raise HTTPException(status_code=400, detail="No CLIP embeddings found for reference images. Run embedding extraction first, or upload local reference images.")
        updated = await _score_paginated((Image.id, col), (col.isnot(None),), _cosine_chunk(ref_embs), defer_write=True)
        if updated == 0:
            raise HTTPException(status_code=400, detail="No CLIP embeddings found for dataset images. Run embedding extraction first.")
        return {"updated": updated, "skipped": total_images - updated, **_UNBLENDED_FINAL}

    # --- DINOv2 branch ---
    if body.embedding_type == "dino":
        if body.dino_layer is None:
            # Final layer (current behaviour)
            col = Image.dino_embedding
            ref_embs = []
            if body.reference_image_ids:
                ref_rows_raw = await _select_by_ids(db, (Image.id, col), body.reference_image_ids)
                ref_embs.extend(r[1] for r in ref_rows_raw if r[1] is not None)
            for b64 in body.reference_embeddings:
                ref_embs.append(base64.b64decode(b64))
            if not ref_embs:
                raise HTTPException(status_code=400, detail="No DINOv2 embeddings found for reference images. Run embedding extraction first.")
            updated = await _score_paginated((Image.id, col), (col.isnot(None),), _cosine_chunk(ref_embs), defer_write=True)
            if updated == 0:
                raise HTTPException(status_code=400, detail="No DINOv2 embeddings found for dataset images. Run embedding extraction first.")
            return {"updated": updated, "skipped": total_images - updated, **_UNBLENDED_FINAL}
        else:
            # Per-layer mode
            layer = body.dino_layer
            if not (1 <= layer <= 12):
                raise HTTPException(status_code=422, detail="dino_layer must be between 1 and 12.")
            col = Image.dino_layer_embeddings
            ref_embs = []
            if body.reference_image_ids:
                ref_rows_raw = await _select_by_ids(db, (Image.id, col), body.reference_image_ids)
                ref_embs.extend(
                    slice_layer_embedding(r[1], layer)
                    for r in ref_rows_raw if r[1] is not None
                )
            for b64 in body.reference_embeddings:
                ref_embs.append(slice_layer_embedding(base64.b64decode(b64), layer))
            if not ref_embs:
                raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for reference images. Run per-layer embedding extraction first.")
            updated = await _score_paginated((Image.id, col), (col.isnot(None),), _cosine_chunk(ref_embs, layer), defer_write=True)
            if updated == 0:
                raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for dataset images. Run per-layer embedding extraction first.")
            return {"updated": updated, "skipped": total_images - updated,
                    "dino_layer": layer, "clip_weight": None, "dino_weight": None}

    # --- Combined branch ---
    if body.embedding_type in ("combined", "combined_all_layers"):
        if body.reference_embeddings:
            raise HTTPException(status_code=400, detail="External reference files are CLIP-only. Combined mode requires reference images from the dataset.")
        layer = body.dino_layer  # None → use dino_embedding; int → use dino_layer_embeddings slice
        if layer is not None and not (1 <= layer <= 12):
            raise HTTPException(status_code=422, detail="dino_layer must be between 1 and 12.")
        use_layer_col = layer is not None or body.embedding_type == "combined_all_layers"

        # Fetch refs — always need clip; dino column depends on mode
        if body.reference_image_ids:
            if use_layer_col:
                ref_result = await _select_by_ids(
                    db, (Image.id, Image.clip_embedding, Image.dino_layer_embeddings), body.reference_image_ids
                )
                ref_rows = [(r[0], r[1], r[2]) for r in ref_result if r[1] is not None and r[2] is not None]
            else:
                ref_result = await _select_by_ids(
                    db, (Image.id, Image.clip_embedding, Image.dino_embedding), body.reference_image_ids
                )
                ref_rows = [(r[0], r[1], r[2]) for r in ref_result if r[1] is not None and r[2] is not None]
        else:
            ref_rows = []
        if not ref_rows:
            detail = (
                "No images with both CLIP and per-layer DINOv2 embeddings found among reference images. Run per-layer embedding extraction first."
                if use_layer_col else
                "No images with both CLIP and DINOv2 embeddings found among reference images. Run embedding extraction (CLIP + DINOv2) first."
            )
            raise HTTPException(status_code=400, detail=detail)

        ref_clip = [r[1] for r in ref_rows]
        ref_dino_raw = [r[2] for r in ref_rows]  # either dino_embedding bytes or dino_layer_embeddings bytes

        if body.embedding_type == "combined_all_layers":
            # Score CLIP + each DINOv2 layer independently, store in dino_layer_scores.
            # Reference work is hoisted out of the per-candidate loop: the CLIP score
            # doesn't depend on the layer (one _cosine_sim call per chunk), and the
            # per-layer normalized mean reference is computed once here.
            mean_refs = _mean_layer_refs(ref_dino_raw)  # (12, 768)

            def _score_chunk_combined(chunk_rows) -> list[dict]:
                ids = [r[0] for r in chunk_rows]
                clip_blobs = [r[1] for r in chunk_rows]
                dino_blobs = [r[2] for r in chunk_rows]
                clip_scores = _cosine_sim(ref_clip, clip_blobs)  # (N,), rounded to 4
                cand = np.stack([_decode_dino_layers(b) for b in dino_blobs])  # (N, 12, 768)
                # Per-layer cosine sim to the normalized mean ref (matches compute_style_similarity).
                dino_by_layer = [cand[:, l, :] @ mean_refs[l] for l in range(_DINO_LAYERS)]
                # One `blend_scores` call per layer (12 per chunk) rather than
                # `12 × N` inline expressions — the single source of truth for the
                # weights, and strictly fewer Python-level operations. The CLIP
                # hoist above is untouched.
                blended_by_layer = [
                    blend_scores(
                        clip_scores,
                        [float(round(float(v), 4)) for v in dino_by_layer[l]],
                    )
                    for l in range(_DINO_LAYERS)
                ]
                results = []
                for n, img_id in enumerate(ids):
                    # A fresh dict per row, written through a Core `update(Image)`
                    # that never loaded the old value — so `CLAUDE.md`'s
                    # never-mutate-a-JSON-column rule is satisfied by construction.
                    # Do not "optimise" this into a merge onto the existing dict.
                    layer_scores: dict[str, float] = {
                        str(l + 1): blended_by_layer[l][n] for l in range(_DINO_LAYERS)
                    }
                    results.append({"id": img_id, "dino_layer_scores": layer_scores, "style_similarity_score": layer_scores[str(DEFAULT_DINO_LAYER)]})
                return results

            updated = await _score_paginated(
                (Image.id, Image.clip_embedding, Image.dino_layer_embeddings),
                (Image.clip_embedding.isnot(None), Image.dino_layer_embeddings.isnot(None)),
                _score_chunk_combined,
            )
            if updated == 0:
                raise HTTPException(status_code=400, detail="No dataset images have both CLIP and per-layer DINOv2 embeddings. Run per-layer embedding extraction first.")
            return {"updated": updated, "skipped": total_images - updated,
                    "dino_layer": DEFAULT_DINO_LAYER,
                    "clip_weight": STYLE_CLIP_WEIGHT, "dino_weight": STYLE_DINO_WEIGHT}

        # Single layer or final layer combined score
        dino_cand_col = Image.dino_layer_embeddings if use_layer_col else Image.dino_embedding
        ref_dino = (
            [slice_layer_embedding(b, layer) for b in ref_dino_raw] if layer is not None else ref_dino_raw
        )

        def _score_chunk_combined_one(chunk_rows) -> list[dict]:
            cand_clip = [r[1] for r in chunk_rows]
            cand_dino = [r[2] for r in chunk_rows]
            if layer is not None:
                cand_dino = [slice_layer_embedding(b, layer) for b in cand_dino]
            scores = compute_combined_similarity(ref_clip, cand_clip, ref_dino, cand_dino)
            return [{"id": r[0], "style_similarity_score": s} for r, s in zip(chunk_rows, scores)]

        updated = await _score_paginated(
            (Image.id, Image.clip_embedding, dino_cand_col),
            (Image.clip_embedding.isnot(None), dino_cand_col.isnot(None)),
            _score_chunk_combined_one,
            defer_write=True,
        )
        if updated == 0:
            detail = (
                "No dataset images have both CLIP and per-layer DINOv2 embeddings. Run per-layer embedding extraction first."
                if use_layer_col else
                "No dataset images have both CLIP and DINOv2 embeddings. Run embedding extraction (CLIP + DINOv2) first."
            )
            raise HTTPException(status_code=400, detail=detail)
        # `layer` is None when the run used `dino_embedding`, the post-layernorm
        # final vector — which is not any of the twelve numbered layers, so None is
        # the honest record rather than a missing one.
        return {"updated": updated, "skipped": total_images - updated,
                "dino_layer": layer,
                "clip_weight": STYLE_CLIP_WEIGHT, "dino_weight": STYLE_DINO_WEIGHT}

    # --- All DINOv2 layers branch ---
    if body.embedding_type == "dino_all_layers":
        col = Image.dino_layer_embeddings
        ref_blobs: list[bytes] = []
        if body.reference_image_ids:
            ref_rows_raw = await _select_by_ids(db, (Image.id, col), body.reference_image_ids)
            ref_blobs.extend(r[1] for r in ref_rows_raw if r[1] is not None)
        if not ref_blobs:
            raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for reference images. Run per-layer embedding extraction first.")

        # Per-layer normalized mean reference, computed once (hoisted out of the loop).
        mean_refs = _mean_layer_refs(ref_blobs)  # (12, 768)

        def _score_chunk_dino(chunk_rows) -> list[dict]:
            ids = [r[0] for r in chunk_rows]
            dino_blobs = [r[1] for r in chunk_rows]
            cand = np.stack([_decode_dino_layers(b) for b in dino_blobs])  # (N, 12, 768)
            dino_by_layer = [cand[:, l, :] @ mean_refs[l] for l in range(_DINO_LAYERS)]
            results = []
            for n, img_id in enumerate(ids):
                layer_scores = {str(l + 1): float(round(float(dino_by_layer[l][n]), 4)) for l in range(_DINO_LAYERS)}
                results.append({"id": img_id, "dino_layer_scores": layer_scores, "style_similarity_score": layer_scores[str(DEFAULT_DINO_LAYER)]})
            return results

        updated = await _score_paginated(
            (Image.id, Image.dino_layer_embeddings),
            (Image.dino_layer_embeddings.isnot(None),),
            _score_chunk_dino,
        )
        if updated == 0:
            raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for dataset images. Run per-layer embedding extraction first.")
        return {"updated": updated, "skipped": total_images - updated,
                "dino_layer": DEFAULT_DINO_LAYER, "clip_weight": None, "dino_weight": None}

    raise HTTPException(status_code=422, detail=f"Unknown embedding_type '{body.embedding_type}'. Use 'clip', 'dino', 'combined', 'dino_all_layers', or 'combined_all_layers'.")


@router.get("/style-similarity/{dataset_id}")
async def style_similarity_distribution(dataset_id: str, db: AsyncSession = Depends(get_db)):
    """The dataset's style-score distribution, plus what produced those scores.

    `style_similarity_score` is a raw cosine whose scale depends entirely on the
    mode that made it, so the number alone is unreadable. The quantiles let a
    client place one image inside its own dataset's distribution — mode-invariant
    by construction — and `run` says which mode that was.

    Twenty-one breakpoints, every 5th percentile: ±2.5 pp worst case for ~200
    bytes. 101 is more resolution than a 4-px meter can spend, and deciles are
    visibly chunky exactly where style matching cares (the top of the range).

    **Dataset-wide, with no `subfolder` parameter.** The same image must read the
    same in a filtered pane, an unfiltered pane and on the detail page.
    `StyleSimilarityRequest` has no subfolder concept either, and
    `ix_images_dataset_similarity` makes the ordered scan covering.

    Like its sibling `aesthetic_coverage`, an unknown dataset gets an empty
    payload rather than a 404 — the caller is a gallery card, not a navigation.
    """
    dist = await get_style_distribution(db, dataset_id)
    run = (await db.execute(
        select(StyleSimilarityRun).where(StyleSimilarityRun.dataset_id == dataset_id)
    )).scalar_one_or_none()

    return {
        **dist,
        # Hand-built field by field, so a column added to `StyleSimilarityRun`
        # without a line here is invisible: no error, just a field the client never
        # sees. Same trap `CLAUDE.md` names for `DatasetOut.video_count`.
        "run": None if run is None else {
            "embedding_type": run.embedding_type,
            "dino_layer": run.dino_layer,
            "clip_weight": run.clip_weight,
            "dino_weight": run.dino_weight,
            "reference_image_ids": run.reference_image_ids or [],
            "reference_count": run.reference_count,
            "external_reference_count": run.external_reference_count,
            "scored_count": run.scored_count,
            "skipped_count": run.skipped_count,
            "scoped_image_count": run.scoped_image_count,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        },
    }


@router.get("/duplicates/{dataset_id}")
async def get_duplicates(dataset_id: str, db: AsyncSession = Depends(get_db)):
    """Duplicate clusters, each led by the image the scan decided to **keep**.

    `_flag_duplicates` flags only `group[1:]`, so the root — the image every other
    member's `duplicate_of` points at — carries no `is_duplicate` flag and the
    query below cannot see it. Returning the flagged rows alone rendered a 2-image
    pair as a "group" of one and made *Keep best* / *Keep first* unable to reduce a
    cluster to a single image: the one they were meant to keep was never in the
    payload, so resolving deleted every copy the scan had found.

    The root is fetched separately and prepended, marked `kept` so the UI can
    render it distinctly and so `group[0]` — what *Keep first* keeps — is the one
    the scan chose. The flag itself deliberately stays **off** the root: bulk
    filters, the Stats "flagged" counts and export exclusions all read
    `is_duplicate` and must keep seeing only the removable copies.

    Ordered by `created_at`, which is also in the payload, so member order is the
    dataset's own history rather than whatever order SQLite happened to scan in.

    **Frame lineage is annotated here, not in the scan.** Frames from one video —
    held animation cels, recycled footage, a locked-off shot — land inside Hamming
    8 and get grouped, and *Keep best* then deletes them with nothing on screen
    saying they share a source. Teaching `_flag_duplicates` to skip same-source
    pairs would be the wrong fix twice over: `is_duplicate` feeds bulk filters,
    the Stats flagged counts, export exclusions and the gallery badge, so changing
    what the scan flags has a blast radius this does not need; and two frames from
    one shot often *are* duplicates the user wants gone. The defect is silence.
    So the four lineage fields ride along on every row and the client decides how
    loudly to say it.

    The response stays a **list of lists**. Promoting a group to an object with a
    `same_source` field would break `DuplicateGroup` and every assertion in
    `test_duplicate_groups_http.py` for a boolean the client derives in one line.
    """
    def _row(img: Image, *, kept: bool) -> dict:
        return {
            "id": img.id,
            "filename": img.filename,
            "aesthetic_score": img.aesthetic_score,
            # Which model produced that score. One line covering both flagged
            # members and the prepended root — the root path has regressed
            # separately before (see the docstring above), and a group whose root
            # carried no marker would look uniform to the mixed-model guard that
            # keeps *Keep best* from ranking two incomparable scales.
            "aesthetic_model": img.aesthetic_model,
            "updated_at": img.updated_at,
            "created_at": img.created_at,
            "kept": kept,
            "source_video_id": img.source_video_id,
            "source_timestamp_ms": img.source_timestamp_ms,
            "source_shot_index": img.source_shot_index,
            "source_video_name": video_names.get(img.source_video_id),
        }

    result = await db.execute(
        select(Image).where(
            Image.dataset_id == dataset_id,
            Image.quality_flags["is_duplicate"].as_boolean() == True,
        ).order_by(Image.created_at)
    )
    duplicates = result.scalars().all()
    flagged_ids = {img.id for img in duplicates}

    # A root that is itself flagged belongs to some other group and is already
    # rendered there; only unflagged roots are missing from the payload.
    #
    # Scoped to this dataset, like the prune's aliveness check: a group only ever
    # exists inside one dataset, so a `duplicate_of` naming a row elsewhere is
    # drift, and resolving it would pull a *foreign* image into the payload as
    # the group's kept root — where *Keep best* could delete it. Out of scope it
    # simply has no row to prepend, which is the case the loop below already
    # documents for a root deleted since the scan.
    group_keys = {img.quality_flags.get("duplicate_of", img.id) for img in duplicates}
    roots: dict[str, Image] = {}
    root_ids = [k for k in group_keys if k not in flagged_ids]
    for chunk in chunked(root_ids):
        res = await db.execute(
            select(Image).where(Image.id.in_(chunk), Image.dataset_id == dataset_id)
        )
        roots.update({img.id: img for img in res.scalars().all()})

    # Video filenames for the lineage annotation, resolved before any row is
    # built so `_row` can read them for duplicates and roots alike. There is no
    # `relationship()` from Image to Video and none should be added — this is the
    # one place that needs the join, and a lazy load on an async session raises
    # MissingGreenlet. A video deleted since extraction leaves the id NULL, and a
    # video whose row is gone but whose id survived leaves the name None; both
    # render as "unknown source", which is honest.
    video_ids = sorted(
        {img.source_video_id for img in [*duplicates, *roots.values()] if img.source_video_id}
    )
    video_names: dict[str, str] = {}
    for chunk in chunked(video_ids):
        res = await db.execute(
            select(Video.id, Video.filename).where(Video.id.in_(chunk))
        )
        video_names.update({r.id: r.filename for r in res.all()})

    groups: dict[str, list] = {}
    for img in duplicates:
        key = img.quality_flags.get("duplicate_of", img.id)
        groups.setdefault(key, []).append(_row(img, kept=False))
    for key, members in groups.items():
        root = roots.get(key)
        # A root deleted since the scan simply has no row to prepend; its
        # group stays a group of the surviving copies.
        if root is not None:
            members.insert(0, _row(root, kept=True))
    return {"groups": list(groups.values())}


@router.post("/duplicates/resolve")
async def resolve_duplicates(body: DuplicateResolve, db: AsyncSession = Depends(get_db)):
    # Every `IN (...)` here is `chunked()`: the Quality page's bulk bar resolves
    # every filtered group in one call, so `delete_ids` is a few thousand rows on
    # a large dataset rather than the handful this endpoint was first written for.
    dataset_ids: set[str] = set()
    deletable: list[str] = []
    in_use = failed = 0
    if body.delete_ids:
        rows = []
        for chunk in chunked(body.delete_ids):
            result = await db.execute(
                select(Image.id, Image.dataset_id, Image.file_path, Image.thumbnail_path)
                .where(Image.id.in_(chunk))
            )
            rows.extend(result.all())
        dataset_ids = {r.dataset_id for r in rows}
        for did in dataset_ids:
            ensure_not_busy(did)
        # Same shape as `images.batch_delete`: files first, per row, gated by
        # `contained_path` — the versioning hook copies the bytes into
        # `{ds}/.versions/objects/`, so an out-of-tree `file_path` is a read
        # primitive even with the unlink skipped. A row whose file is locked is
        # kept (PM-021, GPT-008), and so is its group — see `live_groups` below.
        deletable, in_use, failed = await unlink_files_for_rows(db, rows, context="resolve_duplicates")
        for chunk in chunked(deletable):
            await db.execute(delete(Image).where(Image.id.in_(chunk)))
        await db.commit()

        for did in dataset_ids:
            await refresh_stats(db, did)

    # A group that kept a copy (its file was locked, or would not delete) still
    # has a live duplicate. Clearing its kept image's flags would dissolve it —
    # and the backstop prune below would then clear the lone kept copy as an
    # orphan of a dead root — leaving two identical, unflagged images and nothing
    # for the "close it and try again" the client shows to find. Keyed like
    # `get_duplicates`: a member's `duplicate_of`, or the root's own id.
    gone = set(deletable)
    live_groups: set[str] = set()
    for chunk in chunked([i for i in body.delete_ids if i not in gone]):
        res = await db.execute(select(Image.id, Image.quality_flags).where(Image.id.in_(chunk)))
        for rid, flags in res.all():
            live_groups.add((flags or {}).get("duplicate_of") or rid)

    # One batched load per chunk rather than a `db.get` per id — a bulk resolve
    # keeps one survivor per group, so this loop is as long as the group count.
    # The copy-then-reassign stays exactly as it was: SQLAlchemy compares JSON
    # columns by equality, so mutating `img.quality_flags` in place and
    # reassigning the same dict skips the UPDATE (the CLAUDE.md invariant, pinned
    # by `test_quality_flags_persistence_http.py`).
    for chunk in chunked(body.keep_ids):
        res = await db.execute(select(Image).where(Image.id.in_(chunk)))
        for img in res.scalars().all():
            if ((img.quality_flags or {}).get("duplicate_of") or img.id) in live_groups:
                continue
            flags = dict(img.quality_flags or {})
            flags.pop("is_duplicate", None)
            flags.pop("duplicate_of", None)
            img.quality_flags = flags
    # Backstop for a *partial* resolve: the loop above only clears the ids the
    # caller listed, so deleting a group's root while keeping a survivor the
    # caller did not name would leave that survivor flagged against a row that no
    # longer exists (PM-022). After the loop, so its pending clears are part of
    # the state the prune counts survivors against — which is also why this is
    # the one prune site that does **not** run in the same transaction as its
    # delete: the `keep_ids` clears above land after the delete's own commit, so
    # a failure here leaves drift a Technical re-scan repairs.
    # `dataset_ids` is empty for a keep-only call, which makes this a no-op
    # rather than a needless scan — so a caller that passes `keep_ids` alone and
    # thereby strands the last survivor of an already-dead root leaves it
    # flagged until the next delete in that dataset or a re-scan. Not reachable
    # from the UI: `QualityPage`'s only `resolveDuplicates` call always pairs a
    # group's keep with its own deletes.
    await prune_orphaned_duplicate_flags(db, dataset_ids)
    await db.commit()
    return {"deleted": len(deletable), "in_use": in_use, "failed": failed}
