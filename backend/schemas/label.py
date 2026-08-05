from pydantic import BaseModel

from backend.schemas import UtcDatetime


class LabelOut(BaseModel):
    id: str
    name: str
    color: str
    hotkey: str | None = None
    sort_order: int
    created_at: UtcDatetime
    # How many images carry this label, app-wide. Filled from one GROUP BY in
    # `list_labels`; the Settings panel names it in the delete confirmation.
    usage_count: int = 0

    model_config = {"from_attributes": True}


class LabelCreate(BaseModel):
    name: str
    color: str | None = None
    hotkey: str | None = None


class LabelUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    hotkey: str | None = None


class LabelReorderRequest(BaseModel):
    ordered_ids: list[str]


class LabelAssignRequest(BaseModel):
    """The single attach/detach body.

    One endpoint serves the detail panel (1 image), a hotkey (1 image) and the
    gallery toolbar (up to `SELECT_ALL_ID_CAP`) — two endpoints would be two
    idempotency stories. `add`/`remove` hold label ids.
    """
    image_ids: list[str]
    add: list[str] = []
    remove: list[str] = []
