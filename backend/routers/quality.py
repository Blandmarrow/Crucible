import asyncio
import base64
import functools
import logging

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
from backend.services import version_service
from backend.services.dataset_busy import ensure_not_busy
from backend.services.dataset_service import refresh_stats
from backend.utils import chunked, contained_path, normalize_subfolder
from backend.workers.job_queue import job_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quality", tags=["quality"])

# Per-layer DINOv2 blob layout: 12 transformer layers × 768 dims × float16.
_DINO_LAYERS = 12
_DINO_DIM = 768


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
    embedding_type: str = "clip"  # "clip" | "dino" | "combined"
    dino_layer: int | None = None  # 1–12; only when embedding_type == "dino"


@router.post("/score")
async def score_quality(body: ScoreRequest, db: AsyncSession = Depends(get_db)):
    query = select(Image).where(Image.dataset_id == body.dataset_id)
    if body.image_ids:
        query = query.where(Image.id.in_(body.image_ids))
    elif body.subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(body.subfolder))
    result = await db.execute(query)
    images = result.scalars().all()

    if not images:
        return {"job_id": None, "message": "No images found"}

    checks = [c for c, flag in [
        ("technical", body.run_technical),
        ("aesthetic", body.run_aesthetic),
        ("watermark", body.run_watermark),
        ("embeddings", body.run_embeddings),
        ("DINOv2", body.run_dino),
        ("NSFW", body.run_nsfw),
    ] if flag]
    auto_label = f"Quality: {', '.join(checks) or 'none'} — {len(images)} image{'s' if len(images) != 1 else ''}"
    job = BackgroundJob(
        job_type="quality_score",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=len(images),
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    image_data = [(img.id, img.file_path) for img in images]

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        from backend.ml.aesthetic_scorer import (
            extract_clip_embeddings_batch,
            score_images_batch,
            score_images_watermark,
        )
        from backend.ml.technical_scorer import score_images_technical
        from backend.services.threshold_service import get_thresholds

        async with AsyncSessionLocal() as ts_session:
            thresholds = await get_thresholds(ts_session)

        ids = [d[0] for d in image_data]
        paths = [d[1] for d in image_data]

        loop = asyncio.get_running_loop()

        aesthetic_scores = []
        if body.run_aesthetic:
            entry = await model_manager.load_aesthetic(job_id=job_id, loop=loop, dataset_id=body.dataset_id)
            aesthetic_scores = await score_images_batch(paths, entry.model, job_id=job_id)

        technical_results = []
        if body.run_technical:
            technical_results = await score_images_technical(
                ids, paths, job_id=job_id,
                blur_threshold=thresholds.blur_threshold,
                noise_threshold=thresholds.noise_threshold,
                uniformity_threshold=thresholds.uniformity_threshold,
            )

        watermark_results = []
        if body.run_watermark:
            entry = await model_manager.load_aesthetic(job_id=job_id, loop=loop, dataset_id=body.dataset_id)
            watermark_results = await score_images_watermark(
                paths, entry.model, job_id=job_id,
                watermark_threshold=thresholds.watermark_threshold,
            )

        clip_embeddings: list[bytes | None] = []
        dino_embeddings: list[bytes | None] = []
        dino_layer_embeddings: list[bytes | None] = []
        if body.run_embeddings:
            entry = await model_manager.load_aesthetic(job_id=job_id, loop=loop, dataset_id=body.dataset_id)
            clip_embeddings = await extract_clip_embeddings_batch(paths, entry.model, job_id=job_id)
        if body.run_dino:
            from backend.ml.dino_scorer import extract_embeddings_dino, extract_layer_embeddings_dino
            dino_entry = await model_manager.load_dino(job_id=job_id, loop=loop, dataset_id=body.dataset_id)
            dino_embeddings = await extract_embeddings_dino(paths, dino_entry, job_id=job_id)
            if body.run_dino_layers:
                dino_layer_embeddings = await extract_layer_embeddings_dino(paths, dino_entry, job_id=job_id)

        nsfw_results: list[dict] = []
        if body.run_nsfw:
            from backend.ml.nsfw_scorer import score_images_nsfw_batch
            nsfw_entry = await model_manager.load_nsfw(job_id=job_id, loop=loop, dataset_id=body.dataset_id)
            nsfw_results = await score_images_nsfw_batch(
                paths, nsfw_entry.model, threshold=thresholds.nsfw_threshold, job_id=job_id
            )

        # Any of the *_results lists may be shorter than `ids` if the job was cancelled
        # mid-batch (scorers return partial results). Guard every indexed access with
        # `i < len(...)` so completed work still persists.
        async with AsyncSessionLocal() as session:
            for i, img_id in enumerate(ids):
                img = await session.get(Image, img_id)
                if not img:
                    continue
                if i < len(aesthetic_scores):
                    img.aesthetic_score = aesthetic_scores[i]
                if i < len(technical_results):
                    t = technical_results[i]
                    img.blur_score = t.get("blur_score")
                    img.noise_score = t.get("noise_score")
                    img.uniformity_score = t.get("uniformity_score")
                    img.color_score = t.get("color_score")
                    img.saturation_score = t.get("saturation_score")
                    img.luminance_score = t.get("luminance_score")
                    flags = dict(img.quality_flags or {})
                    flags["is_blurry"] = t.get("is_blurry", False)
                    flags["is_noisy"] = t.get("is_noisy", False)
                    flags["is_uniform"] = t.get("is_uniform", False)
                    img.quality_flags = flags
                if i < len(watermark_results):
                    w = watermark_results[i]
                    img.watermark_score = w.get("watermark_score")
                    flags = dict(img.quality_flags or {})
                    flags["has_watermark"] = w.get("has_watermark", False)
                    img.quality_flags = flags
                if i < len(clip_embeddings):
                    img.clip_embedding = clip_embeddings[i]
                if i < len(dino_embeddings):
                    img.dino_embedding = dino_embeddings[i]
                if i < len(dino_layer_embeddings):
                    img.dino_layer_embeddings = dino_layer_embeddings[i]
                if i < len(nsfw_results):
                    n = nsfw_results[i]
                    img.nsfw_score = n.get("nsfw_score")
                    flags = dict(img.quality_flags or {})
                    flags["is_nsfw"] = n.get("is_nsfw", False)
                    img.quality_flags = flags
            await session.commit()

        # Persist completed work before honoring the cancellation.
        job_queue.raise_if_cancelled(job_id)

        # Detect duplicates after scoring
        if body.run_technical:
            await _flag_duplicates(job_id, body.dataset_id, int(thresholds.duplicate_threshold))

    async def _flag_duplicates(job_id: str, dataset_id: str, duplicate_threshold: int) -> None:
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
        if not dup_of:
            return

        async with AsyncSessionLocal() as session:
            affected_ids = list(dup_of.keys())
            for chunk in chunked(affected_ids):
                result = await session.execute(select(Image).where(Image.id.in_(chunk)))
                for img in result.scalars().all():
                    flags = dict(img.quality_flags or {})
                    flags["is_duplicate"] = True
                    flags["duplicate_of"] = dup_of[img.id]
                    img.quality_flags = flags
            await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": len(images)}


@router.post("/embed-references")
async def embed_references(files: list[UploadFile] = File(...)):
    """Compute CLIP embeddings for uploaded reference images.
    Returns base64-encoded float16 bytes that can be passed to /style-similarity."""
    from backend.ml.aesthetic_scorer import extract_clip_embedding_from_bytes_sync

    entry = await model_manager.load_aesthetic()
    loop = asyncio.get_running_loop()
    embeddings = []
    for f in files:
        img_bytes = await f.read()
        fn = functools.partial(extract_clip_embedding_from_bytes_sync, img_bytes, entry.model)
        emb_bytes = await loop.run_in_executor(None, fn)
        embeddings.append(base64.b64encode(emb_bytes).decode())
    return {"embeddings": embeddings}


@router.post("/style-similarity")
async def compute_style_similarity(
    body: StyleSimilarityRequest,
    db: AsyncSession = Depends(get_db),
):
    from backend.ml.similarity_scorer import compute_style_similarity as _cosine_sim
    from backend.ml.similarity_scorer import compute_combined_similarity

    loop = asyncio.get_running_loop()

    id_filter = (Image.id.in_(body.image_ids),) if body.image_ids else ()
    total_count_result = await db.execute(
        select(func.count(Image.id)).where(Image.dataset_id == body.dataset_id, *id_filter)
    )
    total_images = total_count_result.scalar() or 0

    async def _score_all_layers_paginated(select_cols, extra_filters, score_chunk) -> int:
        """Keyset-paginate the candidate set and score each chunk off the event loop.

        Avoids loading every ~18 KB per-layer blob into one result set (~1.8 GB at
        100k images). `score_chunk(rows) -> list[dict]` runs in an executor; `rows`
        are SQLAlchemy Rows whose first column is `Image.id`. Returns the number of
        rows updated. Used by both all-layers branches.
        """
        last_id = ""
        total_updated = 0
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
                .limit(2000)
            )).all()
            if not chunk_rows:
                break
            updates = await loop.run_in_executor(None, score_chunk, chunk_rows)
            if updates:
                await db.execute(update(Image), updates)
                total_updated += len(updates)
            last_id = chunk_rows[-1][0]
        if total_updated:
            await db.commit()
        return total_updated

    # --- CLIP branch (unchanged behaviour) ---
    if body.embedding_type == "clip":
        col = Image.clip_embedding
        ref_embs: list[bytes] = []
        if body.reference_image_ids:
            ref_result = await db.execute(
                select(Image.id, col).where(Image.id.in_(body.reference_image_ids))
            )
            ref_embs.extend(r[1] for r in ref_result.all() if r[1] is not None)
        for b64 in body.reference_embeddings:
            ref_embs.append(base64.b64decode(b64))
        if not ref_embs:
            raise HTTPException(status_code=400, detail="No CLIP embeddings found for reference images. Run embedding extraction first, or upload local reference images.")
        cand_result = await db.execute(
            select(Image.id, col).where(Image.dataset_id == body.dataset_id, col.isnot(None), *id_filter)
        )
        cand_rows = [(r[0], r[1]) for r in cand_result.all()]
        if not cand_rows:
            raise HTTPException(status_code=400, detail="No CLIP embeddings found for dataset images. Run embedding extraction first.")
        scores = await loop.run_in_executor(None, _cosine_sim, ref_embs, [r[1] for r in cand_rows])
        await db.execute(update(Image), [{"id": img_id, "style_similarity_score": s} for (img_id, _), s in zip(cand_rows, scores)])
        await db.commit()
        return {"updated": len(cand_rows), "skipped": total_images - len(cand_rows)}

    # --- DINOv2 branch ---
    if body.embedding_type == "dino":
        if body.dino_layer is None:
            # Final layer (current behaviour)
            col = Image.dino_embedding
            ref_embs = []
            if body.reference_image_ids:
                ref_result = await db.execute(
                    select(Image.id, col).where(Image.id.in_(body.reference_image_ids))
                )
                ref_embs.extend(r[1] for r in ref_result.all() if r[1] is not None)
            for b64 in body.reference_embeddings:
                ref_embs.append(base64.b64decode(b64))
            if not ref_embs:
                raise HTTPException(status_code=400, detail="No DINOv2 embeddings found for reference images. Run embedding extraction first.")
            cand_result = await db.execute(
                select(Image.id, col).where(Image.dataset_id == body.dataset_id, col.isnot(None), *id_filter)
            )
            cand_rows = [(r[0], r[1]) for r in cand_result.all()]
            if not cand_rows:
                raise HTTPException(status_code=400, detail="No DINOv2 embeddings found for dataset images. Run embedding extraction first.")
            scores = await loop.run_in_executor(None, _cosine_sim, ref_embs, [r[1] for r in cand_rows])
            await db.execute(update(Image), [{"id": img_id, "style_similarity_score": s} for (img_id, _), s in zip(cand_rows, scores)])
            await db.commit()
            return {"updated": len(cand_rows), "skipped": total_images - len(cand_rows)}
        else:
            # Per-layer mode
            from backend.ml.dino_scorer import slice_layer_embedding
            layer = body.dino_layer
            if not (1 <= layer <= 12):
                raise HTTPException(status_code=422, detail="dino_layer must be between 1 and 12.")
            col = Image.dino_layer_embeddings
            ref_embs = []
            if body.reference_image_ids:
                ref_result = await db.execute(
                    select(Image.id, col).where(Image.id.in_(body.reference_image_ids))
                )
                ref_embs.extend(
                    slice_layer_embedding(r[1], layer)
                    for r in ref_result.all() if r[1] is not None
                )
            for b64 in body.reference_embeddings:
                ref_embs.append(slice_layer_embedding(base64.b64decode(b64), layer))
            if not ref_embs:
                raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for reference images. Run per-layer embedding extraction first.")
            cand_result = await db.execute(
                select(Image.id, col).where(Image.dataset_id == body.dataset_id, col.isnot(None), *id_filter)
            )
            cand_rows_raw = [(r[0], r[1]) for r in cand_result.all()]
            cand_rows = [(img_id, slice_layer_embedding(blob, layer)) for img_id, blob in cand_rows_raw]
            if not cand_rows:
                raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for dataset images. Run per-layer embedding extraction first.")
            scores = await loop.run_in_executor(None, _cosine_sim, ref_embs, [r[1] for r in cand_rows])
            await db.execute(update(Image), [{"id": img_id, "style_similarity_score": s} for (img_id, _), s in zip(cand_rows, scores)])
            await db.commit()
            return {"updated": len(cand_rows), "skipped": total_images - len(cand_rows)}

    # --- Combined branch ---
    if body.embedding_type in ("combined", "combined_all_layers"):
        from backend.ml.dino_scorer import slice_layer_embedding
        if body.reference_embeddings:
            raise HTTPException(status_code=400, detail="External reference files are CLIP-only. Combined mode requires reference images from the dataset.")
        layer = body.dino_layer  # None → use dino_embedding; int → use dino_layer_embeddings slice
        if layer is not None and not (1 <= layer <= 12):
            raise HTTPException(status_code=422, detail="dino_layer must be between 1 and 12.")
        use_layer_col = layer is not None or body.embedding_type == "combined_all_layers"

        # Fetch refs — always need clip; dino column depends on mode
        if body.reference_image_ids:
            if use_layer_col:
                ref_result = await db.execute(
                    select(Image.id, Image.clip_embedding, Image.dino_layer_embeddings)
                    .where(Image.id.in_(body.reference_image_ids))
                )
                ref_rows = [(r[0], r[1], r[2]) for r in ref_result.all() if r[1] is not None and r[2] is not None]
            else:
                ref_result = await db.execute(
                    select(Image.id, Image.clip_embedding, Image.dino_embedding)
                    .where(Image.id.in_(body.reference_image_ids))
                )
                ref_rows = [(r[0], r[1], r[2]) for r in ref_result.all() if r[1] is not None and r[2] is not None]
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
                results = []
                for n, img_id in enumerate(ids):
                    layer_scores: dict[str, float] = {}
                    for l in range(_DINO_LAYERS):
                        d = float(round(float(dino_by_layer[l][n]), 4))
                        layer_scores[str(l + 1)] = round(0.38 * clip_scores[n] + 0.62 * d, 4)
                    results.append({"id": img_id, "dino_layer_scores": layer_scores, "style_similarity_score": layer_scores["12"]})
                return results

            updated = await _score_all_layers_paginated(
                (Image.id, Image.clip_embedding, Image.dino_layer_embeddings),
                (Image.clip_embedding.isnot(None), Image.dino_layer_embeddings.isnot(None)),
                _score_chunk_combined,
            )
            if updated == 0:
                raise HTTPException(status_code=400, detail="No dataset images have both CLIP and per-layer DINOv2 embeddings. Run per-layer embedding extraction first.")
            return {"updated": updated, "skipped": total_images - updated}

        # Single layer or final layer combined score
        if use_layer_col:
            cand_result = await db.execute(
                select(Image.id, Image.clip_embedding, Image.dino_layer_embeddings)
                .where(
                    Image.dataset_id == body.dataset_id,
                    Image.clip_embedding.isnot(None),
                    Image.dino_layer_embeddings.isnot(None),
                    *id_filter,
                )
            )
        else:
            cand_result = await db.execute(
                select(Image.id, Image.clip_embedding, Image.dino_embedding)
                .where(
                    Image.dataset_id == body.dataset_id,
                    Image.clip_embedding.isnot(None),
                    Image.dino_embedding.isnot(None),
                    *id_filter,
                )
            )
        cand_rows_full = [(r[0], r[1], r[2]) for r in cand_result.all()]
        if not cand_rows_full:
            detail = (
                "No dataset images have both CLIP and per-layer DINOv2 embeddings. Run per-layer embedding extraction first."
                if use_layer_col else
                "No dataset images have both CLIP and DINOv2 embeddings. Run embedding extraction (CLIP + DINOv2) first."
            )
            raise HTTPException(status_code=400, detail=detail)

        cand_clip = [r[1] for r in cand_rows_full]
        cand_dino_raw = [r[2] for r in cand_rows_full]

        if layer is not None:
            ref_dino = [slice_layer_embedding(b, layer) for b in ref_dino_raw]
            cand_dino = [slice_layer_embedding(b, layer) for b in cand_dino_raw]
        else:
            ref_dino = ref_dino_raw
            cand_dino = cand_dino_raw
        scores = await loop.run_in_executor(None, compute_combined_similarity, ref_clip, cand_clip, ref_dino, cand_dino)
        await db.execute(update(Image), [{"id": r[0], "style_similarity_score": s} for r, s in zip(cand_rows_full, scores)])
        await db.commit()
        return {"updated": len(cand_rows_full), "skipped": total_images - len(cand_rows_full)}

    # --- All DINOv2 layers branch ---
    if body.embedding_type == "dino_all_layers":
        col = Image.dino_layer_embeddings
        ref_blobs: list[bytes] = []
        if body.reference_image_ids:
            ref_result = await db.execute(
                select(Image.id, col).where(Image.id.in_(body.reference_image_ids))
            )
            ref_blobs.extend(r[1] for r in ref_result.all() if r[1] is not None)
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
                results.append({"id": img_id, "dino_layer_scores": layer_scores, "style_similarity_score": layer_scores["12"]})
            return results

        updated = await _score_all_layers_paginated(
            (Image.id, Image.dino_layer_embeddings),
            (Image.dino_layer_embeddings.isnot(None),),
            _score_chunk_dino,
        )
        if updated == 0:
            raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for dataset images. Run per-layer embedding extraction first.")
        return {"updated": updated, "skipped": total_images - updated}

    raise HTTPException(status_code=422, detail=f"Unknown embedding_type '{body.embedding_type}'. Use 'clip', 'dino', 'combined', or 'dino_all_layers'.")


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
    """
    def _row(img: Image, *, kept: bool) -> dict:
        return {
            "id": img.id,
            "filename": img.filename,
            "aesthetic_score": img.aesthetic_score,
            "updated_at": img.updated_at,
            "created_at": img.created_at,
            "kept": kept,
        }

    result = await db.execute(
        select(Image).where(
            Image.dataset_id == dataset_id,
            Image.quality_flags["is_duplicate"].as_boolean() == True,
        ).order_by(Image.created_at)
    )
    duplicates = result.scalars().all()
    flagged_ids = {img.id for img in duplicates}
    groups: dict[str, list] = {}
    for img in duplicates:
        key = img.quality_flags.get("duplicate_of", img.id)
        groups.setdefault(key, []).append(_row(img, kept=False))

    # A root that is itself flagged belongs to some other group and is already
    # rendered there; only unflagged roots are missing from the payload.
    root_ids = [k for k in groups if k not in flagged_ids]
    if root_ids:
        roots: dict[str, Image] = {}
        for chunk in chunked(root_ids):
            res = await db.execute(select(Image).where(Image.id.in_(chunk)))
            roots.update({img.id: img for img in res.scalars().all()})
        for key, members in groups.items():
            root = roots.get(key)
            # A root deleted since the scan simply has no row to prepend; its
            # group stays a group of the surviving copies.
            if root is not None:
                members.insert(0, _row(root, kept=True))
    return {"groups": list(groups.values())}


@router.post("/duplicates/resolve", status_code=204)
async def resolve_duplicates(body: DuplicateResolve, db: AsyncSession = Depends(get_db)):
    if body.delete_ids:
        result = await db.execute(
            select(Image.id, Image.dataset_id, Image.file_path, Image.thumbnail_path)
            .where(Image.id.in_(body.delete_ids))
        )
        rows = result.all()
        dataset_ids = {r.dataset_id for r in rows}
        for did in dataset_ids:
            ensure_not_busy(did)
        files_to_delete: list[Path] = []
        for r in rows:
            # Same shape as `images.batch_delete`: gate per row, unlink the
            # *resolved* path, and gate the versioning hook with it — it copies the
            # bytes into `{ds}/.versions/objects/`, so an out-of-tree `file_path`
            # is a read primitive even with the unlink skipped. The row delete
            # below is unconditional.
            p = contained_path(
                r.file_path, settings.datasets_dir, context="resolve_duplicates", ident=r.id
            )
            if p is not None:
                await version_service.mark_image_deleted_in_versions(r.id, str(p), db)
                files_to_delete.extend([p, p.with_suffix(".txt")])
            t = contained_path(
                r.thumbnail_path, settings.datasets_dir, context="resolve_duplicates", ident=r.id
            )
            if t is not None:
                files_to_delete.append(t)

        await db.execute(delete(Image).where(Image.id.in_(body.delete_ids)))
        await db.commit()

        for f in files_to_delete:
            f.unlink(missing_ok=True)

        for did in dataset_ids:
            await refresh_stats(db, did)

    for img_id in body.keep_ids:
        img = await db.get(Image, img_id)
        if img:
            flags = dict(img.quality_flags or {})
            flags.pop("is_duplicate", None)
            flags.pop("duplicate_of", None)
            img.quality_flags = flags
    await db.commit()
