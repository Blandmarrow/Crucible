import asyncio
import base64
import functools

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
from backend.utils import normalize_subfolder
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/quality", tags=["quality"])


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
    label: str | None = None


class DuplicateResolve(BaseModel):
    keep_ids: list[str]
    delete_ids: list[str]


class StyleSimilarityRequest(BaseModel):
    dataset_id: str
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

        loop = asyncio.get_event_loop()

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

        async with AsyncSessionLocal() as session:
            for i, img_id in enumerate(ids):
                img = await session.get(Image, img_id)
                if not img:
                    continue
                if aesthetic_scores:
                    img.aesthetic_score = aesthetic_scores[i]
                if technical_results:
                    t = technical_results[i]
                    img.blur_score = t.get("blur_score")
                    img.noise_score = t.get("noise_score")
                    img.uniformity_score = t.get("uniformity_score")
                    img.color_score = t.get("color_score")
                    img.saturation_score = t.get("saturation_score")
                    flags = img.quality_flags or {}
                    flags["is_blurry"] = t.get("is_blurry", False)
                    flags["is_noisy"] = t.get("is_noisy", False)
                    flags["is_uniform"] = t.get("is_uniform", False)
                    img.quality_flags = flags
                if watermark_results:
                    w = watermark_results[i]
                    img.watermark_score = w.get("watermark_score")
                    flags = img.quality_flags or {}
                    flags["has_watermark"] = w.get("has_watermark", False)
                    img.quality_flags = flags
                if clip_embeddings:
                    img.clip_embedding = clip_embeddings[i]
                if dino_embeddings:
                    img.dino_embedding = dino_embeddings[i]
                if dino_layer_embeddings:
                    img.dino_layer_embeddings = dino_layer_embeddings[i]
            await session.commit()

        # Detect duplicates after scoring
        if body.run_technical:
            await _flag_duplicates(body.dataset_id, int(thresholds.duplicate_threshold))

    async def _flag_duplicates(dataset_id: str, duplicate_threshold: int) -> None:
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

        fn = functools.partial(find_duplicates_sync, phashes, duplicate_threshold)
        groups = await asyncio.get_running_loop().run_in_executor(None, fn)

        async with AsyncSessionLocal() as session:
            for group in groups:
                keep = group[0]
                for dup_id in group[1:]:
                    img = await session.get(Image, dup_id)
                    if img:
                        flags = img.quality_flags or {}
                        flags["is_duplicate"] = True
                        flags["duplicate_of"] = keep
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
    loop = asyncio.get_event_loop()
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

    loop = asyncio.get_event_loop()

    total_count_result = await db.execute(
        select(func.count(Image.id)).where(Image.dataset_id == body.dataset_id)
    )
    total_images = total_count_result.scalar() or 0

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
            select(Image.id, col).where(Image.dataset_id == body.dataset_id, col.isnot(None))
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
                select(Image.id, col).where(Image.dataset_id == body.dataset_id, col.isnot(None))
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
                select(Image.id, col).where(Image.dataset_id == body.dataset_id, col.isnot(None))
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

        # Fetch candidates
        if use_layer_col:
            cand_result = await db.execute(
                select(Image.id, Image.clip_embedding, Image.dino_layer_embeddings)
                .where(
                    Image.dataset_id == body.dataset_id,
                    Image.clip_embedding.isnot(None),
                    Image.dino_layer_embeddings.isnot(None),
                )
            )
        else:
            cand_result = await db.execute(
                select(Image.id, Image.clip_embedding, Image.dino_embedding)
                .where(
                    Image.dataset_id == body.dataset_id,
                    Image.clip_embedding.isnot(None),
                    Image.dino_embedding.isnot(None),
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

        if body.embedding_type == "combined_all_layers":
            # Score CLIP + each DINOv2 layer independently, store in dino_layer_scores
            def _combined_all_layers() -> list[dict]:
                results = []
                for i, (img_id, _, blob) in enumerate(cand_rows_full):
                    layer_scores: dict[str, float] = {}
                    for lyr in range(1, 13):
                        r_slices = [slice_layer_embedding(b, lyr) for b in ref_dino_raw]
                        c_slice = slice_layer_embedding(blob, lyr)
                        dino_scores = _cosine_sim(r_slices, [c_slice])
                        clip_scores = _cosine_sim(ref_clip, [cand_clip[i]])
                        layer_scores[str(lyr)] = round(0.38 * clip_scores[0] + 0.62 * dino_scores[0], 4)
                    results.append({"id": img_id, "dino_layer_scores": layer_scores, "style_similarity_score": layer_scores["12"]})
                return results

            updates = await loop.run_in_executor(None, _combined_all_layers)
            await db.execute(update(Image), updates)
            await db.commit()
            return {"updated": len(updates), "skipped": total_images - len(updates)}
        else:
            # Single layer or final layer combined score
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
        from backend.ml.dino_scorer import slice_layer_embedding
        col = Image.dino_layer_embeddings
        ref_blobs: list[bytes] = []
        if body.reference_image_ids:
            ref_result = await db.execute(
                select(Image.id, col).where(Image.id.in_(body.reference_image_ids))
            )
            ref_blobs.extend(r[1] for r in ref_result.all() if r[1] is not None)
        if not ref_blobs:
            raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for reference images. Run per-layer embedding extraction first.")
        cand_result = await db.execute(
            select(Image.id, col).where(Image.dataset_id == body.dataset_id, col.isnot(None))
        )
        cand_rows_blobs = [(r[0], r[1]) for r in cand_result.all()]
        if not cand_rows_blobs:
            raise HTTPException(status_code=400, detail="No per-layer DINOv2 embeddings found for dataset images. Run per-layer embedding extraction first.")

        # Compute similarity for each layer independently, in a single executor call
        def _all_layer_scores() -> list[dict]:
            results = []
            for img_id, blob in cand_rows_blobs:
                layer_scores: dict[str, float] = {}
                for layer in range(1, 13):
                    r_slices = [slice_layer_embedding(b, layer) for b in ref_blobs]
                    c_slice = slice_layer_embedding(blob, layer)
                    score = _cosine_sim(r_slices, [c_slice])[0]
                    layer_scores[str(layer)] = score
                results.append({"id": img_id, "dino_layer_scores": layer_scores, "style_similarity_score": layer_scores["12"]})
            return results

        updates = await loop.run_in_executor(None, _all_layer_scores)
        await db.execute(update(Image), updates)
        await db.commit()
        return {"updated": len(updates), "skipped": total_images - len(updates)}

    raise HTTPException(status_code=422, detail=f"Unknown embedding_type '{body.embedding_type}'. Use 'clip', 'dino', 'combined', or 'dino_all_layers'.")


@router.get("/duplicates/{dataset_id}")
async def get_duplicates(dataset_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Image).where(
            Image.dataset_id == dataset_id,
            Image.quality_flags["is_duplicate"].as_boolean() == True,
        )
    )
    duplicates = result.scalars().all()
    groups: dict[str, list] = {}
    for img in duplicates:
        key = img.quality_flags.get("duplicate_of", img.id)
        groups.setdefault(key, []).append({
            "id": img.id,
            "filename": img.filename,
            "aesthetic_score": img.aesthetic_score,
            "updated_at": img.updated_at,
        })
    return {"groups": list(groups.values())}


@router.post("/duplicates/resolve", status_code=204)
async def resolve_duplicates(body: DuplicateResolve, db: AsyncSession = Depends(get_db)):
    if body.delete_ids:
        result = await db.execute(
            select(Image.id, Image.file_path, Image.thumbnail_path)
            .where(Image.id.in_(body.delete_ids))
        )
        rows = result.all()
        files_to_delete: list[Path] = []
        for r in rows:
            p = Path(r.file_path)
            files_to_delete.append(p)
            files_to_delete.append(p.with_suffix(".txt"))
            if r.thumbnail_path:
                files_to_delete.append(Path(r.thumbnail_path))

        await db.execute(delete(Image).where(Image.id.in_(body.delete_ids)))
        await db.commit()

        for f in files_to_delete:
            f.unlink(missing_ok=True)

    for img_id in body.keep_ids:
        img = await db.get(Image, img_id)
        if img:
            flags = img.quality_flags or {}
            flags.pop("is_duplicate", None)
            flags.pop("duplicate_of", None)
            img.quality_flags = flags
    await db.commit()
