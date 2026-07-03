from typing import Any
from pydantic import BaseModel

from backend.schemas import UtcDatetime


class JobOut(BaseModel):
    id: str
    job_type: str
    label: str | None
    status: str
    dataset_id: str | None
    total_items: int
    done_items: int
    error_msg: str | None
    result_data: dict[str, Any]
    config: dict[str, Any]
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None

    model_config = {"from_attributes": True}


class JobProgress(BaseModel):
    type: str = "progress"
    job_id: str
    job_type: str
    label: str | None = None
    status: str
    done: int
    total: int
    percent: float
    current_item: str = ""
    message: str = ""
