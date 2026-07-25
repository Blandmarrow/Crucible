import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import BackgroundJob
from backend.utils import (
    ALLOWED_FLAG_KEYS,
    InsufficientDiskSpaceError,
    normalize_license_filter,
    parse_license_filter_param,
    require_free_space,
    sanitize_abs_path,
)
from backend.services.export_service import (
    export_aitoolkit,
    export_kohya,
    export_plain,
    preview_export,
)
from backend.workers.job_queue import job_queue

router = APIRouter(prefix="/export", tags=["export"])


class KohyaExportRequest(BaseModel):
    dataset_id: str
    output_dir: str
    n_repeats: int = Field(default=10, ge=1, le=1000)
    concept_token: str = "concept"
    image_ids: list[str] | None = None
    output_format: str = "original"
    jpeg_quality: int = 95
    caption_format: str = "txt"
    resize_to: int | None = None
    aesthetic_min: float | None = None
    captioned_only: bool = False
    exclude_flags: str = ""   # comma-separated flag names
    style_sim_min: float | None = None
    subfolders: list[str] | None = None
    strip_metadata: bool = False
    captions_only: bool = False
    export_masks: bool = False
    mask_labels: list[str] | None = None   # None/empty = all detection labels
    mask_exclude_labels: list[str] | None = None   # regions always painted black
    mask_invert: bool = False
    mask_missing: Literal["white", "skip"] = "white"
    # None/empty = no license restriction. Values are effective license ids
    # ("" matches images with no license recorded at either level).
    license_filter: list[str] | None = None
    commercial_only: bool = False
    # Drops images with no effective license. Separate from license_filter, which
    # is an allowlist of known ids and would also drop `other:<free text>` values.
    exclude_unlicensed: bool = False
    # Drops CC BY-ND and friends: an export ships resized/cropped copies, which
    # is what "no derivatives" forbids redistributing.
    exclude_no_derivatives: bool = False
    label: str | None = None


class AIToolkitExportRequest(BaseModel):
    dataset_id: str
    output_dir: str
    concept_name: str = "concept"
    image_ids: list[str] | None = None
    output_format: str = "original"
    jpeg_quality: int = 95
    caption_format: str = "txt"
    resize_to: int | None = None
    aesthetic_min: float | None = None
    captioned_only: bool = False
    exclude_flags: str = ""
    style_sim_min: float | None = None
    subfolders: list[str] | None = None
    strip_metadata: bool = False
    captions_only: bool = False
    export_masks: bool = False
    mask_labels: list[str] | None = None
    mask_exclude_labels: list[str] | None = None
    mask_invert: bool = False
    mask_missing: Literal["white", "skip"] = "white"
    # None/empty = no license restriction. Values are effective license ids
    # ("" matches images with no license recorded at either level).
    license_filter: list[str] | None = None
    commercial_only: bool = False
    # Drops images with no effective license. Separate from license_filter, which
    # is an allowlist of known ids and would also drop `other:<free text>` values.
    exclude_unlicensed: bool = False
    # Drops CC BY-ND and friends: an export ships resized/cropped copies, which
    # is what "no derivatives" forbids redistributing.
    exclude_no_derivatives: bool = False
    label: str | None = None


class PlainExportRequest(BaseModel):
    dataset_id: str
    output_dir: str
    image_ids: list[str] | None = None
    output_format: str = "original"
    jpeg_quality: int = 95
    resize_to: int | None = None
    aesthetic_min: float | None = None
    captioned_only: bool = False
    exclude_flags: str = ""
    style_sim_min: float | None = None
    subfolders: list[str] | None = None
    strip_metadata: bool = False
    captions_only: bool = False
    export_masks: bool = False
    mask_labels: list[str] | None = None
    mask_exclude_labels: list[str] | None = None
    mask_invert: bool = False
    mask_missing: Literal["white", "skip"] = "white"
    # None/empty = no license restriction. Values are effective license ids
    # ("" matches images with no license recorded at either level).
    license_filter: list[str] | None = None
    commercial_only: bool = False
    # Drops images with no effective license. Separate from license_filter, which
    # is an allowlist of known ids and would also drop `other:<free text>` values.
    exclude_unlicensed: bool = False
    # Drops CC BY-ND and friends: an export ships resized/cropped copies, which
    # is what "no derivatives" forbids redistributing.
    exclude_no_derivatives: bool = False
    label: str | None = None


def _normalize_mask_labels(labels: list[str] | None) -> list[str] | None:
    """Strip and drop empty entries; an empty selection means all labels."""
    cleaned = [l.strip() for l in (labels or []) if l.strip()]
    return cleaned or None


def _parse_labels_json_param(value: str, param_name: str) -> list[str] | None:
    """Parse a JSON-array-of-strings query param into normalized mask labels.

    Detection labels are free text and may contain commas, so the preview GET
    passes them as a JSON array rather than comma-separated. Empty string means
    the param was omitted (None → all/none per caller semantics).
    """
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{param_name} must be a JSON array of strings")
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise HTTPException(status_code=400, detail=f"{param_name} must be a JSON array of strings")
    return _normalize_mask_labels(parsed)


def _parse_flags(s: str) -> list[str]:
    """Parse a comma-separated flag list, rejecting unknown names with HTTP 400.

    Call this in the request path only. The export handlers enqueue a job and
    return before the coroutine runs, so an HTTPException raised in there would
    fail the job instead of reaching the client.
    """
    flags = [f.strip() for f in s.split(",") if f.strip()]
    invalid = [f for f in flags if f not in ALLOWED_FLAG_KEYS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown flag keys: {invalid}")
    return flags


def _check_output_dir(output_dir: str) -> None:
    """Validate the client-supplied destination and floor-check its free space.

    `sanitize_abs_path` is the standard gate on any path a client hands a router
    (400 on a null byte or a relative path) — an export writes wherever it is told,
    so a relative `output_dir` would land somewhere relative to the server's cwd
    rather than anywhere the user meant.

    The export loop runs the real free-space preflight — it knows the payload size.
    This one only catches an already-full disk, but it answers 507 immediately
    instead of handing back a job id the client has to poll to learn the export
    never started. Both checks belong in the request path: the handlers enqueue a
    job and return, so an HTTPException raised in the coroutine would fail the job
    instead of reaching the client.
    """
    sanitize_abs_path(output_dir)
    try:
        require_free_space(output_dir)
    except InsufficientDiskSpaceError as e:
        raise HTTPException(status_code=507, detail=str(e))


@router.post("/kohya")
async def export_kohya_endpoint(body: KohyaExportRequest, db: AsyncSession = Depends(get_db)):
    from pathlib import Path as _Path
    exclude_flags = _parse_flags(body.exclude_flags)
    _check_output_dir(body.output_dir)
    mask_labels = _normalize_mask_labels(body.mask_labels)
    mask_exclude_labels = _normalize_mask_labels(body.mask_exclude_labels)
    license_filter = normalize_license_filter(body.license_filter)
    auto_label = f"Export kohya — {_Path(body.output_dir).name}"
    job = BackgroundJob(
        job_type="export",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await export_kohya(
                session,
                body.dataset_id,
                body.output_dir,
                body.n_repeats,
                body.concept_token,
                body.image_ids,
                body.output_format,
                body.jpeg_quality,
                body.caption_format,
                body.resize_to,
                body.aesthetic_min,
                body.captioned_only,
                exclude_flags,
                body.style_sim_min,
                body.subfolders,
                body.strip_metadata,
                body.captions_only,
                export_masks=body.export_masks,
                mask_labels=mask_labels,
                mask_exclude_labels=mask_exclude_labels,
                mask_invert=body.mask_invert,
                mask_missing=body.mask_missing,
                license_filter=license_filter,
                commercial_only=body.commercial_only,
                exclude_unlicensed=body.exclude_unlicensed,
                exclude_no_derivatives=body.exclude_no_derivatives,
                job_id=job_id,
            )
        async with AsyncSessionLocal() as session:
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = result
                job_row.total_items = result.get("exported", 0)
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/aitoolkit")
async def export_aitoolkit_endpoint(body: AIToolkitExportRequest, db: AsyncSession = Depends(get_db)):
    from pathlib import Path as _Path
    exclude_flags = _parse_flags(body.exclude_flags)
    _check_output_dir(body.output_dir)
    mask_labels = _normalize_mask_labels(body.mask_labels)
    mask_exclude_labels = _normalize_mask_labels(body.mask_exclude_labels)
    license_filter = normalize_license_filter(body.license_filter)
    auto_label = f"Export ai-toolkit — {_Path(body.output_dir).name}"
    job = BackgroundJob(
        job_type="export",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await export_aitoolkit(
                session,
                body.dataset_id,
                body.output_dir,
                body.concept_name,
                body.image_ids,
                body.output_format,
                body.jpeg_quality,
                body.caption_format,
                body.resize_to,
                body.aesthetic_min,
                body.captioned_only,
                exclude_flags,
                body.style_sim_min,
                body.subfolders,
                body.strip_metadata,
                body.captions_only,
                export_masks=body.export_masks,
                mask_labels=mask_labels,
                mask_exclude_labels=mask_exclude_labels,
                mask_invert=body.mask_invert,
                mask_missing=body.mask_missing,
                license_filter=license_filter,
                commercial_only=body.commercial_only,
                exclude_unlicensed=body.exclude_unlicensed,
                exclude_no_derivatives=body.exclude_no_derivatives,
                job_id=job_id,
            )
        async with AsyncSessionLocal() as session:
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = result
                job_row.total_items = result.get("exported", 0)
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.post("/plain")
async def export_plain_endpoint(body: PlainExportRequest, db: AsyncSession = Depends(get_db)):
    from pathlib import Path as _Path
    exclude_flags = _parse_flags(body.exclude_flags)
    _check_output_dir(body.output_dir)
    mask_labels = _normalize_mask_labels(body.mask_labels)
    mask_exclude_labels = _normalize_mask_labels(body.mask_exclude_labels)
    license_filter = normalize_license_filter(body.license_filter)
    auto_label = f"Export plain — {_Path(body.output_dir).name}"
    job = BackgroundJob(
        job_type="export",
        label=body.label or auto_label,
        dataset_id=body.dataset_id,
        total_items=0,
        config=body.model_dump(),
    )
    db.add(job)
    await db.commit()

    async def _run(job_id: str) -> None:
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await export_plain(
                session,
                body.dataset_id,
                body.output_dir,
                body.image_ids,
                body.output_format,
                body.jpeg_quality,
                body.resize_to,
                body.aesthetic_min,
                body.captioned_only,
                exclude_flags,
                body.style_sim_min,
                body.subfolders,
                body.strip_metadata,
                body.captions_only,
                export_masks=body.export_masks,
                mask_labels=mask_labels,
                mask_exclude_labels=mask_exclude_labels,
                mask_invert=body.mask_invert,
                mask_missing=body.mask_missing,
                license_filter=license_filter,
                commercial_only=body.commercial_only,
                exclude_unlicensed=body.exclude_unlicensed,
                exclude_no_derivatives=body.exclude_no_derivatives,
                job_id=job_id,
            )
        async with AsyncSessionLocal() as session:
            job_row = await session.get(BackgroundJob, job_id)
            if job_row:
                job_row.result_data = result
                job_row.total_items = result.get("exported", 0)
                await session.commit()

    await job_queue.enqueue(job, _run)
    return {"job_id": job.id}


@router.get("/preview/{dataset_id}")
async def preview(
    dataset_id: str,
    aesthetic_min: float | None = Query(default=None),
    captioned_only: bool = Query(default=False),
    exclude_flags: str = Query(default=""),
    style_sim_min: float | None = Query(default=None),
    subfolders: str = Query(default=""),
    export_masks: bool = Query(default=False),
    mask_labels: str = Query(default="", description="JSON array of label strings; empty = all labels"),
    mask_exclude_labels: str = Query(default="", description="JSON array of label strings; regions always painted black"),
    mask_missing: Literal["white", "skip"] = Query(default="white"),
    license_filter: str = Query(default="", description="JSON array of effective license ids; empty = no filter"),
    commercial_only: bool = Query(default=False),
    exclude_unlicensed: bool = Query(default=False),
    exclude_no_derivatives: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
):
    subfolder_list = [s.strip() for s in subfolders.split(",") if s.strip()] or None
    label_list = _parse_labels_json_param(mask_labels, "mask_labels")
    exclude_label_list = _parse_labels_json_param(mask_exclude_labels, "mask_exclude_labels")
    return await preview_export(
        db,
        dataset_id,
        aesthetic_min,
        captioned_only,
        _parse_flags(exclude_flags),
        style_sim_min,
        subfolder_list,
        export_masks=export_masks,
        mask_labels=label_list,
        mask_exclude_labels=exclude_label_list,
        mask_missing=mask_missing,
        license_filter=parse_license_filter_param(license_filter),
        commercial_only=commercial_only,
        exclude_unlicensed=exclude_unlicensed,
        exclude_no_derivatives=exclude_no_derivatives,
    )
