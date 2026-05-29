from datetime import datetime
from typing import Any
from pydantic import BaseModel


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
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

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
