import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ml import ollama_captioner
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
from backend.models.openai_provider import OpenAIProvider
from backend.utils import ALLOWED_FLAG_KEYS, normalize_subfolder
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/captioning", tags=["captioning"])
logger = logging.getLogger(__name__)

_REFUSAL_RE = re.compile(
    r"(I(?:'m| am) (?:sorry|unable|not able)|I cannot|As an AI|I apologize|"
    r"I can't|I won't be able to|[Ss]he (?:is|appears to be) (?:a |an )?(?:fictional|animated|2D|cartoon)|"
    r"This (?:image |photo )?(?:appears|seems) to (?:be|depict)|"
    r"(?:The person|They) (?:appears|seems) to be)[^.!?]*[.!?]?",
    re.IGNORECASE,
)


def _strip_refusals(text: str) -> str:
    return _REFUSAL_RE.sub("", text).strip()


class CaptionJobRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    subfolder: str | None = None
    model: str  # "florence2_large" | "florence2_promptgen" | "paligemma2" | "ollama:model_name" | "openai_compat:{id}:{model}" | "wd14:{variant}"
    style: str = "detailed"
    overwrite: bool = False
    custom_prompt: str = ""
    target_width: int | None = None
    target_height: int | None = None
    append_tags: bool = True
    strip_refusals: bool = True
    save_backup: bool = False
    rename_on_caption: bool = False
    min_aesthetic_score: float | None = None
    exclude_flags: list[str] | None = None
    wd14_threshold: float = 0.35

    @field_validator("exclude_flags")
    @classmethod
    def _validate_flags(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            invalid = [f for f in v if f not in ALLOWED_FLAG_KEYS]
            if invalid:
                raise ValueError(f"Unknown flag keys: {invalid}")
        return v


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)):
    from backend.ml import wd14_tagger
    from backend.schemas.openai_provider import OpenAIProviderOut, _is_remote
    static = model_manager.list_models()
    ollama_models = await ollama_captioner.list_vision_models()
    wd14_models = wd14_tagger.list_wd14_models()
    result = await db.execute(select(OpenAIProvider).order_by(OpenAIProvider.created_at))
    provider_rows = result.scalars().all()
    openai_compat_models = [OpenAIProviderOut.from_orm_row(r) for r in provider_rows]
    return {
        "local_models": static,
        "ollama_models": ollama_models,
        "wd14_models": wd14_models,
        "openai_compat_models": [m.model_dump() for m in openai_compat_models],
    }


@router.get("/styles")
async def list_styles():
    return {
        "florence2": ["short", "detailed", "tags", "dense", "promptgen"],
        "paligemma2": ["short", "detailed", "tags", "booru"],
        "ollama": ["short", "detailed", "tags", "booru"],
    }


@router.post("/run")
async def run_captioning(body: CaptionJobRequest, db: AsyncSession = Depends(get_db)):
    query = (
        select(Image.id, Image.file_path, Image.tags_json, Image.filename, Image.subfolder)
        .where(Image.dataset_id == body.dataset_id)
    )
    if body.image_ids:
        query = query.where(Image.id.in_(body.image_ids))
    elif body.subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(body.subfolder))
    if not body.overwrite:
        query = query.where(Image.caption_text == "")
    if body.min_aesthetic_score is not None:
        query = query.where(Image.aesthetic_score >= body.min_aesthetic_score)
    if body.exclude_flags:
        for flag_key in body.exclude_flags:
            query = query.where(Image.quality_flags[flag_key].as_boolean().is_not(True))
    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return {"job_id": None, "message": "No images to caption"}

    job = BackgroundJob(
        job_type="caption",
        dataset_id=body.dataset_id,
        total_items=len(rows),
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    image_data = [(r.id, r.file_path, r.tags_json or [], r.filename, r.subfolder or "") for r in rows]

    async def _run(job_id: str) -> None:
        import time
        from backend.database import AsyncSessionLocal
        from backend.services.caption_service import set_caption
        from backend.workers.progress import broadcaster
        try:
            import torch as _torch
        except Exception:
            _torch = None

        is_ollama = body.model.startswith("ollama:")
        is_florence = body.model.startswith("florence2")
        is_paligemma = body.model == "paligemma2"
        is_openai_compat = body.model.startswith("openai_compat:")
        is_wd14 = body.model.startswith("wd14:")

        # Load model upfront
        florence_entry = None
        paligemma_entry = None
        ollama_model_name = None
        openai_provider = None
        openai_model_name = None
        wd14_variant = None
        model_label = body.model

        if is_florence:
            variant = "promptgen" if "promptgen" in body.model else "large"
            florence_entry = await model_manager.load_florence2(variant)
            model_label = f"Florence-2 ({variant})"
        elif is_paligemma:
            paligemma_entry = await model_manager.load_paligemma2()
            model_label = "PaliGemma-2"
        elif is_ollama:
            ollama_model_name = body.model.removeprefix("ollama:")
            model_label = f"Ollama ({ollama_model_name})"
        elif is_openai_compat:
            # Format: openai_compat:{provider_id}:{model_name}
            parts = body.model.split(":", 2)
            provider_id = parts[1] if len(parts) > 1 else ""
            openai_model_name = parts[2] if len(parts) > 2 else ""
            async with AsyncSessionLocal() as _ps:
                openai_provider = await _ps.get(OpenAIProvider, provider_id)
            if openai_provider is None:
                raise RuntimeError(f"OpenAI-compat provider '{provider_id}' not found")
            if not openai_model_name:
                openai_model_name = openai_provider.default_model
            model_label = f"{openai_provider.name} ({openai_model_name})"
        elif is_wd14:
            wd14_variant = body.model.removeprefix("wd14:")
            model_label = f"WD14 ({wd14_variant})"

        total = len(image_data)
        start_time = time.monotonic()
        failed_image_ids: list[str] = []
        # Cache VRAM reading every 10 images to avoid per-image GPU calls
        cached_vram_mb = 0

        _rename_db_names: set[str] = set()
        if body.rename_on_caption:
            from backend.utils import rename_with_sidecar, slugify_filename, unique_filename
            async with AsyncSessionLocal() as _ns:
                _r = await _ns.execute(select(Image.filename).where(Image.dataset_id == body.dataset_id))
                _rename_db_names = {r[0] for r in _r.all()}

        async with AsyncSessionLocal() as session:
            for i, (img_id, file_path, existing_tags, img_filename, img_subfolder) in enumerate(image_data):
                # Check for user-initiated stop before each image
                async with AsyncSessionLocal() as cs:
                    job_row = await cs.get(BackgroundJob, job_id)
                    if job_row and job_row.status == "cancelled":
                        raise asyncio.CancelledError()

                # Generate caption for this image
                caption = ""
                try:
                    if is_florence:
                        from backend.ml.florence_captioner import caption_image as _fi
                        caption = await _fi(file_path, florence_entry, body.style,
                                            body.target_width, body.target_height)
                    elif is_paligemma:
                        from backend.ml.paligemma_captioner import caption_image as _pi
                        caption = await _pi(file_path, paligemma_entry, body.style,
                                            body.target_width, body.target_height)
                    elif is_ollama:
                        caption = await ollama_captioner.caption_image(
                            file_path, ollama_model_name, body.style, body.custom_prompt,
                            body.target_width, body.target_height,
                        )
                    elif is_openai_compat:
                        from backend.ml.openai_compat_captioner import caption_image as _oi
                        caption = await _oi(
                            file_path,
                            base_url=openai_provider.base_url,
                            api_key=openai_provider.api_key,
                            model_name=openai_model_name,
                            style=body.style,
                            custom_prompt=body.custom_prompt,
                            max_px=openai_provider.max_image_px,
                            max_tokens=openai_provider.max_tokens,
                            target_w=body.target_width,
                            target_h=body.target_height,
                        )
                    elif is_wd14:
                        from backend.ml import wd14_tagger
                        caption = await asyncio.get_event_loop().run_in_executor(
                            None, wd14_tagger.tag_image_sync, file_path, wd14_variant, body.wd14_threshold
                        )
                except Exception:
                    logger.error("Caption failed for %s", file_path, exc_info=True)
                    failed_image_ids.append(img_id)

                # Save immediately if a caption was produced
                if caption:
                    if body.strip_refusals:
                        caption = _strip_refusals(caption)
                    if caption:
                        if body.style in ("tags", "booru"):
                            tags = [t.strip() for t in caption.split(",") if t.strip()]
                            if body.append_tags and existing_tags:
                                existing_set = set(tags)
                                tags = tags + [t for t in existing_tags if t not in existing_set]
                                caption = ", ".join(tags)
                        else:
                            tags = []
                            if body.append_tags and existing_tags:
                                caption = caption.rstrip() + ", " + ", ".join(existing_tags)

                        if body.save_backup:
                            txt_path = Path(file_path).with_suffix(".txt")
                            if txt_path.exists():
                                bak_path = txt_path.with_suffix(".txt.bak")
                                bak_path.write_text(txt_path.read_text(encoding="utf-8"), encoding="utf-8")

                        await set_caption(session, img_id, caption, tags, body.style, body.model)

                        if body.rename_on_caption:
                            try:
                                new_stem = slugify_filename(img_subfolder.replace("/", "_")) if img_subfolder else "image"
                                old_path = Path(file_path)
                                suf = old_path.suffix.lower()
                                _rename_db_names.discard(img_filename)
                                new_filename = unique_filename(old_path.parent, new_stem, suf, _rename_db_names)
                                new_path = old_path.parent / new_filename
                                if new_path != old_path:
                                    rename_with_sidecar(old_path, new_path)
                                await session.execute(
                                    sa_update(Image).where(Image.id == img_id).values(
                                        filename=new_filename,
                                        file_path=str(new_path),
                                        is_auto_named=True,
                                    )
                                )
                                _rename_db_names.add(new_filename)
                                await session.commit()
                            except Exception:
                                logger.error("Rename failed for %s", file_path, exc_info=True)

                # Refresh VRAM reading only every 10 images (GPU call is not free)
                if i % 10 == 0 and _torch and _torch.cuda.is_available():
                    cached_vram_mb = int(_torch.cuda.memory_reserved() / 1024 / 1024)

                elapsed = time.monotonic() - start_time
                throughput = round((i + 1) / elapsed, 2) if elapsed > 0 else 0
                filename = Path(file_path).name
                await broadcaster.emit(job_id, {
                    "type": "progress",
                    "job_id": job_id,
                    "job_type": "caption",
                    "status": "running",
                    "done": i + 1,
                    "total": total,
                    "percent": round((i + 1) / total * 100, 1),
                    "current_item": filename,
                    "image_id": img_id,
                    "message": f"{model_label}: {i + 1}/{total}",
                    "throughput_ips": throughput,
                    "vram_used_mb": cached_vram_mb,
                })

        # Emit a summary event so the frontend can surface any failures to the user
        if failed_image_ids:
            await broadcaster.emit(job_id, {
                "type": "caption_summary",
                "job_id": job_id,
                "job_type": "caption",
                "failed_count": len(failed_image_ids),
                "failed_image_ids": failed_image_ids,
            })

        from backend.services.dataset_service import refresh_stats
        async with AsyncSessionLocal() as session:
            await refresh_stats(session, body.dataset_id)

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id, "total": len(rows)}


@router.delete("/model/{model_id}/unload", status_code=204)
async def unload_model(model_id: str):
    await model_manager.unload(model_id)


# ---------------------------------------------------------------------------
# Caption pipeline
# ---------------------------------------------------------------------------

class PipelineStep(BaseModel):
    model: str
    style: str = "detailed"
    custom_prompt: str = ""
    overwrite: bool = True
    append_tags: bool = False
    strip_refusals: bool = True
    wd14_threshold: float = 0.35


class CaptionPipelineRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    subfolder: str | None = None
    steps: list[PipelineStep]
    save_backup: bool = False
    rename_on_caption: bool = False
    min_aesthetic_score: float | None = None
    exclude_flags: list[str] | None = None

    @field_validator("exclude_flags")
    @classmethod
    def _validate_flags(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            invalid = [f for f in v if f not in ALLOWED_FLAG_KEYS]
            if invalid:
                raise ValueError(f"Unknown flag keys: {invalid}")
        return v

    @field_validator("steps")
    @classmethod
    def _validate_steps(cls, v: list[PipelineStep]) -> list[PipelineStep]:
        if len(v) < 2:
            raise ValueError("Pipeline requires at least 2 steps")
        return v


@router.post("/pipeline")
async def run_pipeline(body: CaptionPipelineRequest, db: AsyncSession = Depends(get_db)):
    query = (
        select(Image.id, Image.file_path, Image.tags_json, Image.filename, Image.subfolder)
        .where(Image.dataset_id == body.dataset_id)
    )
    if body.image_ids:
        query = query.where(Image.id.in_(body.image_ids))
    elif body.subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(body.subfolder))
    if body.min_aesthetic_score is not None:
        query = query.where(Image.aesthetic_score >= body.min_aesthetic_score)
    if body.exclude_flags:
        for flag_key in body.exclude_flags:
            query = query.where(Image.quality_flags[flag_key].as_boolean().is_not(True))
    result = await db.execute(query)
    rows = result.all()

    if not rows:
        return {"job_id": None, "message": "No images to caption"}

    job = BackgroundJob(
        job_type="caption_pipeline",
        dataset_id=body.dataset_id,
        total_items=len(rows) * len(body.steps),
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    image_data = [(r.id, r.file_path, r.tags_json or [], r.filename, r.subfolder or "") for r in rows]

    async def _run_pipeline_job(job_id: str) -> None:
        import time
        from backend.database import AsyncSessionLocal
        from backend.services.caption_service import set_caption
        from backend.workers.progress import broadcaster
        try:
            import torch as _torch
        except Exception:
            _torch = None

        total_images = len(image_data)
        num_steps = len(body.steps)
        overall_done = 0
        overall_total = total_images * num_steps
        start_time = time.monotonic()
        failed_image_ids: set[str] = set()

        for step_idx, step in enumerate(body.steps):
            is_ollama = step.model.startswith("ollama:")
            is_florence = step.model.startswith("florence2")
            is_paligemma = step.model == "paligemma2"
            is_openai_compat = step.model.startswith("openai_compat:")
            is_wd14 = step.model.startswith("wd14:")

            florence_entry = None
            paligemma_entry = None
            ollama_model_name = None
            openai_provider = None
            openai_model_name = None
            wd14_variant = None
            model_label = step.model

            if is_florence:
                variant = "promptgen" if "promptgen" in step.model else "large"
                florence_entry = await model_manager.load_florence2(variant)
                model_label = f"Florence-2 ({variant})"
            elif is_paligemma:
                paligemma_entry = await model_manager.load_paligemma2()
                model_label = "PaliGemma-2"
            elif is_ollama:
                ollama_model_name = step.model.removeprefix("ollama:")
                model_label = f"Ollama ({ollama_model_name})"
            elif is_openai_compat:
                parts = step.model.split(":", 2)
                provider_id = parts[1] if len(parts) > 1 else ""
                openai_model_name = parts[2] if len(parts) > 2 else ""
                async with AsyncSessionLocal() as _ps:
                    openai_provider = await _ps.get(OpenAIProvider, provider_id)
                if openai_provider is None:
                    logger.error("Pipeline step %d: OpenAI-compat provider %s not found", step_idx + 1, provider_id)
                    overall_done += total_images
                    continue
                if not openai_model_name:
                    openai_model_name = openai_provider.default_model
                model_label = f"{openai_provider.name} ({openai_model_name})"
            elif is_wd14:
                wd14_variant = step.model.removeprefix("wd14:")
                model_label = f"WD14 ({wd14_variant})"

            # Load current captions from DB for {previous_caption} substitution
            prev_captions: dict[str, str] = {}
            if step_idx > 0:
                async with AsyncSessionLocal() as _cs:
                    ids = [r[0] for r in image_data]
                    caption_result = await _cs.execute(
                        select(Image.id, Image.caption_text).where(Image.id.in_(ids))
                    )
                    prev_captions = {row.id: (row.caption_text or "") for row in caption_result}

            async with AsyncSessionLocal() as session:
                cached_vram_mb = 0
                for i, (img_id, file_path, existing_tags, img_filename, img_subfolder) in enumerate(image_data):
                    # Cancellation check
                    async with AsyncSessionLocal() as cs:
                        job_row = await cs.get(BackgroundJob, job_id)
                        if job_row and job_row.status == "cancelled":
                            raise asyncio.CancelledError()

                    prev_caption = prev_captions.get(img_id, "")
                    resolved_prompt = step.custom_prompt.replace("{previous_caption}", prev_caption)

                    caption = ""
                    try:
                        if is_florence:
                            from backend.ml.florence_captioner import caption_image as _fi
                            caption = await _fi(file_path, florence_entry, step.style, None, None)
                        elif is_paligemma:
                            from backend.ml.paligemma_captioner import caption_image as _pi
                            caption = await _pi(file_path, paligemma_entry, step.style, None, None)
                        elif is_ollama:
                            caption = await ollama_captioner.caption_image(
                                file_path, ollama_model_name, step.style, resolved_prompt, None, None
                            )
                        elif is_openai_compat:
                            from backend.ml.openai_compat_captioner import caption_image as _oi
                            caption = await _oi(
                                file_path,
                                base_url=openai_provider.base_url,
                                api_key=openai_provider.api_key,
                                model_name=openai_model_name,
                                style=step.style,
                                custom_prompt=resolved_prompt,
                                max_px=openai_provider.max_image_px,
                                max_tokens=openai_provider.max_tokens,
                            )
                        elif is_wd14:
                            from backend.ml import wd14_tagger
                            caption = await asyncio.get_event_loop().run_in_executor(
                                None, wd14_tagger.tag_image_sync, file_path, wd14_variant, step.wd14_threshold
                            )
                    except Exception:
                        logger.error("Pipeline step %d caption failed for %s", step_idx + 1, file_path, exc_info=True)
                        failed_image_ids.add(img_id)

                    if caption:
                        if step.strip_refusals:
                            caption = _strip_refusals(caption)
                        if caption:
                            tags: list[str] = []
                            if step.style in ("tags", "booru") or is_wd14:
                                tags = [t.strip() for t in caption.split(",") if t.strip()]
                                if step.append_tags and existing_tags:
                                    existing_set = set(tags)
                                    tags = tags + [t for t in existing_tags if t not in existing_set]
                                    caption = ", ".join(tags)
                            await set_caption(session, img_id, caption, tags, step.style, step.model)

                    overall_done += 1
                    if i % 10 == 0 and _torch and _torch.cuda.is_available():
                        cached_vram_mb = int(_torch.cuda.memory_reserved() / 1024 / 1024)

                    elapsed = time.monotonic() - start_time
                    throughput = round(overall_done / elapsed, 2) if elapsed > 0 else 0
                    await broadcaster.emit(job_id, {
                        "type": "progress",
                        "job_id": job_id,
                        "job_type": "caption_pipeline",
                        "status": "running",
                        "done": overall_done,
                        "total": overall_total,
                        "percent": round(overall_done / overall_total * 100, 1),
                        "current_item": Path(file_path).name,
                        "image_id": img_id,
                        "message": f"Step {step_idx + 1}/{num_steps}: {model_label} — {i + 1}/{total_images}",
                        "step_index": step_idx + 1,
                        "step_total": num_steps,
                        "throughput_ips": throughput,
                        "vram_used_mb": cached_vram_mb,
                    })

            # Evict local ML models between steps to free VRAM
            if is_florence or is_paligemma:
                await model_manager.evict_all()

        if failed_image_ids:
            from backend.workers.progress import broadcaster
            await broadcaster.emit(job_id, {
                "type": "caption_summary",
                "job_id": job_id,
                "job_type": "caption_pipeline",
                "failed_count": len(failed_image_ids),
                "failed_image_ids": list(failed_image_ids),
            })

        from backend.services.dataset_service import refresh_stats
        async with AsyncSessionLocal() as session:
            await refresh_stats(session, body.dataset_id)

    await job_queue.enqueue(job, _run_pipeline_job)
    return {"job_id": job.id, "total": len(rows) * len(body.steps)}
