"""The dataset health report (M12 phase 1). See docs/dev/analyst/health.md."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Dataset
from backend.schemas.health import DismissRequest, HealthReport, NoteItemOut
from backend.services import health_service, notes_service

router = APIRouter(prefix="/datasets", tags=["health"])


@router.get("/{dataset_id}/health", response_model=HealthReport)
async def get_health(dataset_id: str, db: AsyncSession = Depends(get_db)):
    report = await health_service.get_health(db, dataset_id)
    if report is None:
        raise HTTPException(404, "Dataset not found")
    return report


@router.post("/{dataset_id}/health/dismiss", response_model=NoteItemOut, status_code=201)
async def dismiss_finding(dataset_id: str, body: DismissRequest, db: AsyncSession = Depends(get_db)):
    """Dismiss a finding with a reason. The dismissal is a `preference` note, so the
    analyst sees the reason as a standing rule; it replaces any earlier dismissal of
    the same finding. Undo it by removing that note."""
    if await db.get(Dataset, dataset_id) is None:
        raise HTTPException(404, "Dataset not found")
    finding = await health_service.find(db, dataset_id, body.finding_id)
    if finding is None:
        raise HTTPException(404, f"No current finding '{body.finding_id}'")
    return await notes_service.dismiss(
        db,
        dataset_id,
        finding_id=finding.id,
        title=finding.title,
        reason=body.reason,
        permanent=body.permanent,
        fingerprint=finding.fingerprint,
    )
