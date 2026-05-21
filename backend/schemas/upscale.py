from pydantic import BaseModel


class UpscaleModelInfo(BaseModel):
    name: str
    path: str
    scale: int | None


class UpscaleRunRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None  # None = whole dataset
    model_path: str
    replace: bool = False
    target_width: int | None = None
    target_height: int | None = None
