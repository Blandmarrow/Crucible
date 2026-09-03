import asyncio
import logging
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ml import ollama_captioner
from backend.ml.model_manager import model_manager
from backend.models import BackgroundJob, Image
from backend.models.openai_provider import OpenAIProvider
from backend.utils import ALLOWED_FLAG_KEYS, normalize_subfolder, subsume_tags
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/captioning", tags=["captioning"])
logger = logging.getLogger(__name__)

# Cap on how many per-file error details a caption job's result_data retains
# (failed_count keeps the full tally). Twin of the constant in
# backend/services/dataset_service.py — a cap, not shared logic, so it is
# restated rather than imported across modules.
_MAX_FAILED_DETAILS = 50

try:
    from openai import APITimeoutError
except ImportError:  # pragma: no cover - `openai` is not in requirements-ci.txt
    # A stand-in so the narrow `except` branches below stay well-formed on an
    # install without the SDK. Nothing raises it, so they simply never match.
    class APITimeoutError(Exception):  # type: ignore[no-redef]
        pass


def _failure_headline(timed_out: int, provider: object) -> str | None:
    """One sentence naming *why* images failed, for the badge and the Logs row.

    None when nothing timed out, so the caller's badge keeps its existing generic
    wording — only a timeout has a diagnosis specific enough to be worth stating.
    """
    if not timed_out:
        return None
    return (
        f"{timed_out} image(s) timed out — provider "
        f"'{getattr(provider, 'name', '?')}' did not respond within its "
        f"{getattr(provider, 'timeout_s', '?')}s timeout. "
        "Raise Timeout in Settings \u2192 LLM Providers."
    )


_REFUSAL_RE = re.compile(
    r"(I(?:'m| am) (?:sorry|unable|not able)|I cannot|As an AI|I apologize|"
    r"I can't|I won't be able to|[Ss]he (?:is|appears to be) (?:a |an )?(?:fictional|animated|2D|cartoon)|"
    r"This (?:image |photo )?(?:appears|seems) to (?:be depicting|depict)|"
    r"(?:The person|They) (?:appears|seems) to be (?:a |an )?(?:fictional|animated|2D|cartoon|real|identifiable))[^.!?]*[.!?]?",
    re.IGNORECASE,
)


def _strip_refusals(text: str) -> str:
    return _REFUSAL_RE.sub("", text).strip()


# ── Thinking-block detection & stripping ──────────────────────────────────────

_THINKING_TAG_RE = re.compile(
    r"<think(?:ing)?>\s*.*?\s*</think(?:ing)?>\s*",
    re.DOTALL | re.IGNORECASE,
)

# Matches a thinking preamble at the very start of the text (first line only)
_THINKING_PREAMBLE_RE = re.compile(
    r"^(?:Let me (?:think|analyze|consider|examine|look at)"
    r"|I(?:'ll| will) (?:analyze|examine|describe|look at)"
    r"|Alright[,.]?\s+(?:let(?:'s| me)|I(?:'ll| will)))",
    re.IGNORECASE,
)


def _has_thinking(text: str) -> bool:
    if _THINKING_TAG_RE.search(text):
        return True
    if "\n" in text:
        preamble = text[: text.index("\n")].strip()
        if _THINKING_PREAMBLE_RE.match(preamble):
            return True
    return False


def _strip_thinking(text: str) -> str:
    text = _THINKING_TAG_RE.sub("", text).strip()
    if "\n" in text:
        preamble, _, rest = text.partition("\n")
        if rest.strip() and _THINKING_PREAMBLE_RE.match(preamble.strip()):
            text = rest.strip()
    return text


# ── Underscore normalisation (word_word → word word in prose) ─────────────────

_UNDERSCORE_RE = re.compile(r"(?<=\w)_(?=\w)")


def _has_underscores(text: str) -> bool:
    return bool(_UNDERSCORE_RE.search(text))


def _normalize_underscores(text: str) -> str:
    return _UNDERSCORE_RE.sub(" ", text)


# ── Hedging-phrase detection & stripping (phrase-level) ───────────────────────

_HEDGE_PREFIX_RE = re.compile(
    r"^(?:"
    r"It (?:appears|seems)(?: to \w+\s*)?"
    r"|It (?:looks|comes across) (?:like |as (?:if |though ))?"
    r"|This (?:appears|seems)(?: to \w+\s*)?"
    r"|This (?:looks|comes across) (?:like |as (?:if |though ))?"
    r"|The (?:image|photo|picture|photograph) (?:appears|seems|looks)(?: to \w+\s*)?"
    r"|(?:Possibly|Likely|Perhaps|Maybe|Presumably),?\s+"
    r"|I (?:believe|think|would say),?\s+"
    r"|It (?:could|might|may) be(?: that)?,?\s+"
    r"|Looking at (?:this image|the image|this),?\s+"
    r"|Upon (?:closer )?(?:inspection|examination),?\s+"
    r")",
    re.IGNORECASE,
)


def _has_hedges(text: str) -> bool:
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _HEDGE_PREFIX_RE.match(sentence):
            return True
    return False


def _strip_hedges(text: str) -> str:
    def _process(sentence: str) -> str:
        m = _HEDGE_PREFIX_RE.match(sentence)
        if not m:
            return sentence
        remainder = sentence[m.end():].lstrip()
        if not remainder:
            return ""
        return remainder[0].upper() + remainder[1:]

    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(s for s in (_process(p) for p in parts) if s)


class CaptionJobRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    subfolder: str | None = None
    # Accepted ids are exactly the prefixes `_caption_backend` recognises — see there.
    model: str
    style: str = "detailed"
    overwrite: bool = False
    custom_prompt: str = ""
    target_width: int | None = None
    target_height: int | None = None
    append_tags: bool = True
    strip_refusals: bool = True
    strip_thinking: bool = False
    strip_underscores: bool = False
    strip_hedges: bool = False
    dedupe_tags: bool = False
    save_backup: bool = False
    rename_on_caption: bool = False
    min_aesthetic_score: float | None = None
    exclude_flags: list[str] | None = None
    wd14_threshold: float = 0.35
    label: str | None = None
    delimiter_mode: Literal["overwrite", "append", "prepend"] = "overwrite"
    delimiter: str = ", "

    @field_validator("exclude_flags")
    @classmethod
    def _validate_flags(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            invalid = [f for f in v if f not in ALLOWED_FLAG_KEYS]
            if invalid:
                raise ValueError(f"Unknown flag keys: {invalid}")
        return v

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        if _caption_backend(v) is None:
            raise ValueError(f"'{v}' is not a captioning model")
        return v


_TAG_STYLES = frozenset({"tags", "booru", "danbooru", "e621", "rule34", "booru_like"})


def _backup_sidecar(image_path: str) -> None:
    """Copy an image's caption sidecar to `.txt.bak` before it is overwritten.

    Sync, so the caller can run it in one executor hop — the captioning loop is
    GPU-dominated, so a hop per image costs nothing next to the inference it sits
    between, and keeps two blocking file operations off the event loop.
    """
    txt_path = Path(image_path).with_suffix(".txt")
    if txt_path.exists():
        bak_path = txt_path.with_suffix(".txt.bak")
        bak_path.write_text(txt_path.read_text(encoding="utf-8"), encoding="utf-8")


def _model_short_label(model: str) -> str:
    if model.startswith("florence2"):
        variant = model.removeprefix("florence2_")
        return f"Florence-2 ({variant})"
    if model == "paligemma2":
        return "PaliGemma-2"
    if model.startswith("joycaption_"):
        variant = model.removeprefix("joycaption_")
        return f"JoyCaption ({variant})"
    if model.startswith("ollama:"):
        return f"Ollama ({model.removeprefix('ollama:')})"
    if model.startswith("openai_compat:"):
        parts = model.split(":", 2)
        model_name = parts[2] if len(parts) > 2 and parts[2] else "default"
        return f"OpenAI-compat ({model_name})"
    if model.startswith("wd14:"):
        return f"WD14 ({model.removeprefix('wd14:')})"
    return model


def _caption_backend(model: str) -> str | None:
    """The dispatch chain's single source of truth: which branch runs for this id.

    Returns the branch name, or None if no branch would run. The per-image loops in
    `/run` and `/pipeline` are if/elif chains on these same prefixes with `caption = ""`
    as the starting value, so an id matching nothing here falls through every branch,
    fails the `if caption:` save and finishes the job green having written nothing.
    The validators below reject that at the door; keep this in step with the chains.
    """
    if model.startswith("florence2"):
        return "florence2"
    if model == "paligemma2":
        return "paligemma2"
    if model.startswith("joycaption_"):
        return "joycaption_"
    if model.startswith("ollama:"):
        return "ollama:"
    if model.startswith("openai_compat:"):
        return "openai_compat:"
    if model.startswith("wd14:"):
        return "wd14:"
    return None


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)):
    from backend.ml import wd14_tagger
    from backend.schemas.openai_provider import OpenAIProviderOut
    # Only captioners: the registry also holds scorers, embedders and detectors, and
    # selecting one here used to produce a green job that wrote nothing. Filtering on
    # the registry's own `kind` rather than an id allowlist keeps this from becoming a
    # second list of model ids that drifts from the registry.
    static = [m for m in model_manager.list_models() if m["kind"] == "caption"]
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


@router.post("/run")
async def run_captioning(body: CaptionJobRequest, db: AsyncSession = Depends(get_db)):
    query = (
        select(Image.id, Image.file_path, Image.filename, Image.subfolder, Image.caption_text)
        .where(Image.dataset_id == body.dataset_id)
    )
    if body.image_ids:
        query = query.where(Image.id.in_(body.image_ids))
    elif body.subfolder is not None:
        query = query.where(Image.subfolder == normalize_subfolder(body.subfolder))
    if not body.overwrite and body.delimiter_mode == "overwrite":
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

    auto_label = f"{_model_short_label(body.model)} — {len(rows)} image{'s' if len(rows) != 1 else ''}"
    job = BackgroundJob(
        job_type="caption",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=len(rows),
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    image_data = [(r.id, r.file_path, r.filename, r.subfolder or "", r.caption_text or "") for r in rows]

    async def _run(job_id: str) -> None:
        import time
        from backend.database import AsyncSessionLocal
        from backend.services.caption_service import set_caption
        from backend.workers.progress import broadcaster
        try:
            from backend.ml.device import memory_reserved_mb as _vram_mb
        except Exception:
            _vram_mb = lambda: 0

        is_ollama = body.model.startswith("ollama:")
        is_florence = body.model.startswith("florence2")
        is_paligemma = body.model == "paligemma2"
        is_joycaption = body.model.startswith("joycaption_")
        is_openai_compat = body.model.startswith("openai_compat:")
        is_wd14 = body.model.startswith("wd14:")

        # Load model upfront
        florence_entry = None
        paligemma_entry = None
        joycaption_entry = None
        ollama_model_name = None
        openai_provider = None
        openai_model_name = None
        wd14_variant = None
        model_label = body.model

        _loop = asyncio.get_running_loop()

        if is_florence:
            variant = "promptgen" if "promptgen" in body.model else "large"
            florence_entry = await model_manager.load_florence2(
                variant, job_id=job_id, loop=_loop, dataset_id=body.dataset_id
            )
            model_label = f"Florence-2 ({variant})"
        elif is_paligemma:
            paligemma_entry = await model_manager.load_paligemma2(
                job_id=job_id, loop=_loop, dataset_id=body.dataset_id
            )
            model_label = "PaliGemma-2"
        elif is_joycaption:
            jc_variant = body.model.removeprefix("joycaption_")
            joycaption_entry = await model_manager.load_joycaption(
                variant=jc_variant, job_id=job_id, loop=_loop, dataset_id=body.dataset_id
            )
            model_label = f"JoyCaption ({jc_variant})"
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
            from backend.ml import wd14_tagger
            wd14_variant = body.model.removeprefix("wd14:")
            await _loop.run_in_executor(
                None, wd14_tagger.ensure_loaded, wd14_variant, job_id, _loop, body.dataset_id
            )
            model_label = f"WD14 ({wd14_variant})"

        total = len(image_data)
        start_time = time.monotonic()
        failed_image_ids: list[str] = []
        # Per-file diagnoses for the durable job row (capped) and the tally the
        # headline is built from. See the result_data write at the tail.
        failed_details: list[dict] = []
        timed_out = 0
        # Cache VRAM reading every 10 images to avoid per-image GPU calls
        cached_vram_mb = 0

        _rename_db_names: set[str] = set()
        _occupied_thumb_stems: set[str] = set()
        _planned_thumb_stems: set[str] = set()
        if body.rename_on_caption:
            from backend.utils import rename_with_sidecar, slugify_filename, thumbnail_path_for, unique_filename_with_thumb
            async with AsyncSessionLocal() as _ns:
                _r = await _ns.execute(select(Image.filename).where(Image.dataset_id == body.dataset_id))
                _rename_db_names = {r[0] for r in _r.all()}
            if image_data:
                _thumb_dir = Path(image_data[0][1]).parent.parent / "thumbnails"
                if _thumb_dir.exists():
                    _occupied_thumb_stems = {p.stem for p in _thumb_dir.glob("*.webp")}

        async with AsyncSessionLocal() as session:
            for i, (img_id, file_path, img_filename, img_subfolder, existing_caption) in enumerate(image_data):
                # Check for user-initiated stop before each image
                job_queue.raise_if_cancelled(job_id)

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
                    elif is_joycaption:
                        from backend.ml.joycaption_captioner import caption_image as _ji
                        caption = await _ji(file_path, joycaption_entry, body.style,
                                            custom_prompt=body.custom_prompt,
                                            target_w=body.target_width, target_h=body.target_height)
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
                            timeout_s=openai_provider.timeout_s,
                            target_w=body.target_width,
                            target_h=body.target_height,
                        )
                    elif is_wd14:
                        from backend.ml import wd14_tagger
                        caption = await asyncio.get_running_loop().run_in_executor(
                            None, wd14_tagger.tag_image_sync, file_path, wd14_variant, body.wd14_threshold
                        )
                    else:
                        # Unreachable: the model validator rejects anything _caption_backend
                        # does not know. Here so a *future* prefix mistake surfaces as a
                        # per-image failure with a traceback rather than a green empty job.
                        raise RuntimeError(f"No captioning backend for model '{body.model}'")
                except APITimeoutError as exc:
                    # Ahead of the broad handler because this is the one failure with
                    # an obvious user-side fix, and a bare traceback for it is what
                    # made the original report read as "nothing happened". The image
                    # still fails — only the diagnosis changes.
                    logger.error(
                        "Caption timed out for %s: provider '%s' did not respond within its "
                        "configured %ss timeout. Raise Timeout in Settings \u2192 LLM Providers.",
                        file_path, getattr(openai_provider, "name", "?"),
                        getattr(openai_provider, "timeout_s", "?"),
                    )
                    timed_out += 1
                    failed_image_ids.append(img_id)
                    if len(failed_details) < _MAX_FAILED_DETAILS:
                        failed_details.append({"file": Path(file_path).name, "error": str(exc) or type(exc).__name__})
                except Exception as exc:
                    logger.error("Caption failed for %s", file_path, exc_info=True)
                    failed_image_ids.append(img_id)
                    if len(failed_details) < _MAX_FAILED_DETAILS:
                        failed_details.append({"file": Path(file_path).name, "error": str(exc) or type(exc).__name__})

                # Save immediately if a caption was produced
                if caption:
                    if body.strip_refusals:
                        caption = _strip_refusals(caption)
                    if caption:
                        # Artifact detection always runs; stripping is optional per checkbox.
                        is_prose_style = body.style not in _TAG_STYLES
                        artifact_detected = False

                        if _has_thinking(caption):
                            artifact_detected = True
                            if body.strip_thinking:
                                caption = _strip_thinking(caption)

                        if caption and body.strip_underscores and is_prose_style:
                            caption = _normalize_underscores(caption)

                        if caption and _has_hedges(caption):
                            artifact_detected = True
                            if body.strip_hedges:
                                caption = _strip_hedges(caption)

                        if body.style in _TAG_STYLES:
                            new_tags = [
                                _normalize_underscores(t.strip()) if body.strip_underscores else t.strip()
                                for t in caption.split(",") if t.strip()
                            ]
                            if body.dedupe_tags:
                                new_tags = subsume_tags(new_tags)
                            if body.append_tags and existing_caption and body.delimiter_mode != "overwrite":
                                existing_tags = [
                                    _normalize_underscores(t.strip()) if body.strip_underscores else t.strip()
                                    for t in existing_caption.split(",") if t.strip()
                                ]
                                existing_set = set(new_tags)
                                new_tags = new_tags + [t for t in existing_tags if t not in existing_set]
                            caption = ", ".join(new_tags)

                        if body.delimiter_mode == "append" and existing_caption:
                            caption = existing_caption + body.delimiter + caption
                        elif body.delimiter_mode == "prepend" and existing_caption:
                            caption = caption + body.delimiter + existing_caption

                        if body.save_backup:
                            await asyncio.get_running_loop().run_in_executor(
                                None, _backup_sidecar, file_path
                            )

                        await set_caption(session, img_id, caption, body.style, body.model,
                                          has_ai_artifacts=artifact_detected)

                        if body.rename_on_caption:
                            try:
                                new_stem = slugify_filename(img_subfolder.replace("/", "_")) if img_subfolder else "image"
                                old_path = Path(file_path)
                                suf = old_path.suffix.lower()
                                _rename_db_names.discard(img_filename)
                                new_filename = unique_filename_with_thumb(
                                    old_path.parent, new_stem, suf,
                                    _rename_db_names, _occupied_thumb_stems, _planned_thumb_stems,
                                )
                                new_path = old_path.parent / new_filename
                                old_thumb = Path(thumbnail_path_for(str(old_path)))
                                new_thumb = Path(thumbnail_path_for(str(new_path)))
                                db_values: dict = dict(filename=new_filename, file_path=str(new_path))
                                if new_path != old_path:
                                    rename_with_sidecar(old_path, new_path)
                                    if old_thumb.exists() and old_thumb != new_thumb:
                                        old_thumb.replace(new_thumb)
                                    db_values["is_auto_named"] = True
                                    db_values["thumbnail_path"] = str(new_thumb)
                                await session.execute(
                                    sa_update(Image).where(Image.id == img_id).values(**db_values)
                                )
                                await session.commit()
                            except Exception:
                                logger.error("Rename failed for %s", file_path, exc_info=True)

                # Refresh VRAM reading only every 10 images (GPU call is not free)
                if i % 10 == 0:
                    cached_vram_mb = _vram_mb()

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

        headline = _failure_headline(timed_out, openai_provider)

        # Emit a summary event so the frontend can surface any failures to the user
        if failed_image_ids:
            await broadcaster.emit(job_id, {
                "type": "caption_summary",
                "job_id": job_id,
                "job_type": "caption",
                "failed_count": len(failed_image_ids),
                "failed_image_ids": failed_image_ids,
                "failure_summary": headline,
            })

        from backend.services.dataset_service import refresh_stats
        async with AsyncSessionLocal() as session:
            # Durable copy of the diagnosis: the job row returns normally and so is
            # marked `completed` with error_msg unset, which is why the Logs page
            # needs result_data to show anything at all. Written before refresh_stats
            # and committed explicitly — that function committing internally is an
            # implementation detail of a different module.
            # Known gap: a *cancelled* run reaches neither tail (raise_if_cancelled
            # propagates out), so it writes no result_data and emits no summary —
            # already true of the SSE event. Surviving cancellation needs a
            # try/finally around the whole loop, a larger change than this warrants.
            if failed_image_ids:
                job_row = await session.get(BackgroundJob, job_id)
                if job_row:
                    job_row.result_data = {
                        "failed_count": len(failed_image_ids),
                        "timed_out": timed_out,
                        "failure_summary": headline,
                        "failed": failed_details,
                    }
                await session.commit()
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
    strip_thinking: bool = False
    strip_underscores: bool = False
    strip_hedges: bool = False
    dedupe_tags: bool = False
    wd14_threshold: float = 0.35
    target_width: int | None = None
    target_height: int | None = None
    delimiter_mode: Literal["overwrite", "append", "prepend"] = "overwrite"
    delimiter: str = ", "

    # On the step rather than on CaptionPipelineRequest, so every step is covered for free.
    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        if _caption_backend(v) is None:
            raise ValueError(f"'{v}' is not a captioning model")
        return v


class CaptionPipelineRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    subfolder: str | None = None
    steps: list[PipelineStep]
    save_backup: bool = False
    rename_on_caption: bool = False
    min_aesthetic_score: float | None = None
    exclude_flags: list[str] | None = None
    label: str | None = None

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
        select(Image.id, Image.file_path, Image.filename, Image.subfolder)
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

    n_img = len(rows)
    n_steps = len(body.steps)
    auto_label = f"Pipeline ({n_steps} steps) — {n_img} image{'s' if n_img != 1 else ''}"
    job = BackgroundJob(
        job_type="caption_pipeline",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=n_img * n_steps,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    image_data = [(r.id, r.file_path, r.filename, r.subfolder or "") for r in rows]

    async def _run_pipeline_job(job_id: str) -> None:
        import time
        from backend.database import AsyncSessionLocal
        from backend.services.caption_service import set_caption
        from backend.workers.progress import broadcaster
        try:
            from backend.ml.device import memory_reserved_mb as _vram_mb
        except Exception:
            _vram_mb = lambda: 0

        total_images = len(image_data)
        num_steps = len(body.steps)
        overall_done = 0
        overall_total = total_images * num_steps
        start_time = time.monotonic()
        failed_image_ids: set[str] = set()
        # See the matching accumulators in /run's loop. The ids are a set here
        # because a pipeline can fail the same image on more than one step.
        failed_details: list[dict] = []
        timed_out = 0

        for step_idx, step in enumerate(body.steps):
            is_ollama = step.model.startswith("ollama:")
            is_florence = step.model.startswith("florence2")
            is_paligemma = step.model == "paligemma2"
            is_joycaption = step.model.startswith("joycaption_")
            is_openai_compat = step.model.startswith("openai_compat:")
            is_wd14 = step.model.startswith("wd14:")

            florence_entry = None
            paligemma_entry = None
            joycaption_entry = None
            ollama_model_name = None
            openai_provider = None
            openai_model_name = None
            wd14_variant = None
            model_label = step.model

            _step_loop = asyncio.get_running_loop()

            if is_florence:
                variant = "promptgen" if "promptgen" in step.model else "large"
                florence_entry = await model_manager.load_florence2(
                    variant, job_id=job_id, loop=_step_loop, dataset_id=body.dataset_id
                )
                model_label = f"Florence-2 ({variant})"
            elif is_paligemma:
                paligemma_entry = await model_manager.load_paligemma2(
                    job_id=job_id, loop=_step_loop, dataset_id=body.dataset_id
                )
                model_label = "PaliGemma-2"
            elif is_joycaption:
                jc_variant = step.model.removeprefix("joycaption_")
                joycaption_entry = await model_manager.load_joycaption(
                    variant=jc_variant, job_id=job_id, loop=_step_loop, dataset_id=body.dataset_id
                )
                model_label = f"JoyCaption ({jc_variant})"
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
                from backend.ml import wd14_tagger
                wd14_variant = step.model.removeprefix("wd14:")
                await _step_loop.run_in_executor(
                    None, wd14_tagger.ensure_loaded, wd14_variant, job_id, _step_loop, body.dataset_id
                )
                model_label = f"WD14 ({wd14_variant})"

            # Load current captions from DB for {previous_caption} substitution and/or delimiter merge
            prev_captions: dict[str, str] = {}
            needs_current = (
                (step_idx > 0 and "{previous_caption}" in step.custom_prompt)
                or step.delimiter_mode != "overwrite"
            )
            if needs_current:
                async with AsyncSessionLocal() as _cs:
                    ids = [r[0] for r in image_data]
                    caption_result = await _cs.execute(
                        select(Image.id, Image.caption_text).where(Image.id.in_(ids))
                    )
                    prev_captions = {row.id: (row.caption_text or "") for row in caption_result}

            async with AsyncSessionLocal() as session:
                cached_vram_mb = 0
                for i, (img_id, file_path, img_filename, img_subfolder) in enumerate(image_data):
                    # Cancellation check
                    job_queue.raise_if_cancelled(job_id)

                    prev_caption = prev_captions.get(img_id, "")
                    resolved_prompt = step.custom_prompt.replace("{previous_caption}", prev_caption)

                    caption = ""
                    try:
                        if is_florence:
                            from backend.ml.florence_captioner import caption_image as _fi
                            caption = await _fi(file_path, florence_entry, step.style, step.target_width, step.target_height)
                        elif is_paligemma:
                            from backend.ml.paligemma_captioner import caption_image as _pi
                            caption = await _pi(file_path, paligemma_entry, step.style, step.target_width, step.target_height)
                        elif is_joycaption:
                            from backend.ml.joycaption_captioner import caption_image as _ji
                            caption = await _ji(file_path, joycaption_entry, step.style,
                                                custom_prompt=resolved_prompt,
                                                target_w=step.target_width, target_h=step.target_height)
                        elif is_ollama:
                            caption = await ollama_captioner.caption_image(
                                file_path, ollama_model_name, step.style, resolved_prompt, step.target_width, step.target_height
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
                                timeout_s=openai_provider.timeout_s,
                                target_w=step.target_width,
                                target_h=step.target_height,
                            )
                        elif is_wd14:
                            from backend.ml import wd14_tagger
                            caption = await asyncio.get_running_loop().run_in_executor(
                                None, wd14_tagger.tag_image_sync, file_path, wd14_variant, step.wd14_threshold
                            )
                        else:
                            # Unreachable — see the matching branch in /run's loop.
                            raise RuntimeError(f"No captioning backend for model '{step.model}'")
                    except APITimeoutError as exc:
                        # See the matching branch in /run's loop.
                        logger.error(
                            "Pipeline step %d timed out for %s: provider '%s' did not respond "
                            "within its configured %ss timeout. Raise Timeout in "
                            "Settings \u2192 LLM Providers.",
                            step_idx + 1, file_path, getattr(openai_provider, "name", "?"),
                            getattr(openai_provider, "timeout_s", "?"),
                        )
                        timed_out += 1
                        failed_image_ids.add(img_id)
                        if len(failed_details) < _MAX_FAILED_DETAILS:
                            failed_details.append({"file": Path(file_path).name, "error": str(exc) or type(exc).__name__})
                    except Exception as exc:
                        logger.error("Pipeline step %d caption failed for %s", step_idx + 1, file_path, exc_info=True)
                        failed_image_ids.add(img_id)
                        if len(failed_details) < _MAX_FAILED_DETAILS:
                            failed_details.append({"file": Path(file_path).name, "error": str(exc) or type(exc).__name__})

                    if caption:
                        if step.strip_refusals:
                            caption = _strip_refusals(caption)
                        if caption:
                            is_prose_style = step.style not in _TAG_STYLES and not is_wd14
                            artifact_detected = False

                            if _has_thinking(caption):
                                artifact_detected = True
                                if step.strip_thinking:
                                    caption = _strip_thinking(caption)

                            if caption and step.strip_underscores and is_prose_style:
                                caption = _normalize_underscores(caption)

                            if caption and _has_hedges(caption):
                                artifact_detected = True
                                if step.strip_hedges:
                                    caption = _strip_hedges(caption)

                            if step.style in _TAG_STYLES or is_wd14:
                                new_tags = [
                                    _normalize_underscores(t.strip()) if step.strip_underscores else t.strip()
                                    for t in caption.split(",") if t.strip()
                                ]
                                if step.dedupe_tags:
                                    new_tags = subsume_tags(new_tags)
                                existing_prev = prev_captions.get(img_id, "")
                                if step.append_tags and existing_prev and step.delimiter_mode != "overwrite":
                                    existing_tags = [
                                        _normalize_underscores(t.strip()) if step.strip_underscores else t.strip()
                                        for t in existing_prev.split(",") if t.strip()
                                    ]
                                    existing_set = set(new_tags)
                                    new_tags = new_tags + [t for t in existing_tags if t not in existing_set]
                                caption = ", ".join(new_tags)
                            existing = prev_captions.get(img_id, "")
                            if step.delimiter_mode == "append" and existing:
                                caption = existing + step.delimiter + caption
                            elif step.delimiter_mode == "prepend" and existing:
                                caption = caption + step.delimiter + existing
                            await set_caption(session, img_id, caption, step.style, step.model,
                                              has_ai_artifacts=artifact_detected)

                    overall_done += 1
                    if i % 10 == 0:
                        cached_vram_mb = _vram_mb()

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
            if is_florence or is_paligemma or is_joycaption:
                await model_manager.evict_all()

        headline = _failure_headline(timed_out, openai_provider)

        if failed_image_ids:
            from backend.workers.progress import broadcaster
            await broadcaster.emit(job_id, {
                "type": "caption_summary",
                "job_id": job_id,
                "job_type": "caption_pipeline",
                "failed_count": len(failed_image_ids),
                "failed_image_ids": list(failed_image_ids),
                "failure_summary": headline,
            })

        from backend.services.dataset_service import refresh_stats
        async with AsyncSessionLocal() as session:
            # See the matching write in /run's tail, including the cancelled-run gap.
            if failed_image_ids:
                job_row = await session.get(BackgroundJob, job_id)
                if job_row:
                    job_row.result_data = {
                        "failed_count": len(failed_image_ids),
                        "timed_out": timed_out,
                        "failure_summary": headline,
                        "failed": failed_details,
                    }
                await session.commit()
            await refresh_stats(session, body.dataset_id)

    await job_queue.enqueue(job, _run_pipeline_job)
    return {"job_id": job.id, "total": len(rows) * len(body.steps)}
