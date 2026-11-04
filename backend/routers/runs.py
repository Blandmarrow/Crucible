"""Automated dataset runs (M6 §4). The logic is `services/run_service.py`."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Run
from backend.schemas.run import RunOut, RunStartRequest
from backend.services import run_service

router = APIRouter(prefix="/runs", tags=["runs"])


# The most folders a run's response lists; `leaf_count` says how many there are.
LEAF_LIST_LIMIT = 200


def _leaves(run: Run) -> list[dict]:
    order = {"abandoned": 0, "open": 1, "met": 2}
    rows = [
        {"path": v.get("path", ""), "target": v.get("target", 0), "kept": (v.get("kept") or [0])[-1],
         "state": v.get("state", "open"), "reason": v.get("reason")}
        for v in ((run.context or {}).get("leaves") or {}).values()
    ]
    rows.sort(key=lambda r: (order.get(r["state"], 1), r["path"]))
    return rows


def _out(run: Run) -> RunOut:
    leaves = _leaves(run)
    return RunOut(
        id=run.id,
        dataset_id=run.dataset_id,
        plan_id=run.plan_id,
        name=run.name,
        recipe=run.recipe or {},
        stages=run.stages or [],
        status=run.status,
        pause_requested=bool(run.pause_requested),
        current_job_id=run.current_job_id,
        error_msg=run.error_msg,
        row_count=len((run.context or {}).get("row_ids") or []),
        outcome=(run.context or {}).get("loop"),
        leaves=leaves[:LEAF_LIST_LIMIT],
        leaf_count=len(leaves),
        met_count=sum(1 for l in leaves if l["state"] == "met"),
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.post("/", response_model=RunOut)
async def start_run(body: RunStartRequest, db: AsyncSession = Depends(get_db)):
    return _out(await run_service.start_run(db, body))


@router.get("/", response_model=list[RunOut])
async def list_runs(
    dataset_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(Run).where(Run.dataset_id == dataset_id).order_by(Run.created_at.desc()).limit(limit)
    )).scalars().all()
    return [_out(r) for r in rows]


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    return _out(await run_service._get(db, run_id))


@router.post("/{run_id}/pause", response_model=RunOut)
async def pause_run(run_id: str, db: AsyncSession = Depends(get_db)):
    return _out(await run_service.pause_run(db, run_id))


@router.post("/{run_id}/resume", response_model=RunOut)
async def resume_run(run_id: str, db: AsyncSession = Depends(get_db)):
    return _out(await run_service.resume_run(db, run_id))


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_db)):
    return _out(await run_service.cancel_run(db, run_id))


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str, db: AsyncSession = Depends(get_db)):
    await run_service.delete_run(db, run_id)
