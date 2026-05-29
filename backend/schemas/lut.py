from pydantic import BaseModel, field_validator


class LutModelInfo(BaseModel):
    name: str
    path: str
    format: str


class LutRunRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    lut_path: str
    intensity: float = 1.0
    replace: bool = False
    subfolder: str | None = None
    label: str | None = None

    @field_validator("intensity")
    @classmethod
    def _clamp_intensity(cls, v: float) -> float:
        return max(0.0, min(1.0, v))
