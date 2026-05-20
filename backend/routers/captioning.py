import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ml import ollama_captioner
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
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
    model: str  # "florence2_large" | "florence2_promptgen" | "paligemma2" | "ollama:model_name"
    style: str = "detailed"
    overwrite: bool = False
    custom_prompt: str = ""
    target_width: int | None = None
    target_height: int | None = None
    append_tags: bool = True
    strip_refusals: bool = True
    save_backup: bool = False
    rename_on_caption: bool = False


@router.get("/models")
async def list_models():
    static = model_manager.list_models()
    ollama_models = await ollama_captioner.list_vision_models()
    return {"local_models": static, "ollama_models": ollama_models}


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
    if not body.overwrite:
        query = query.where(Image.caption_text == "")
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

        # Load model upfront
        florence_entry = None
        paligemma_entry = None
        ollama_model_name = None
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
                    cached_vram_mb = int(_torch.cuda.memory_allocated() / 1024 / 1024)

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
