from datetime import datetime

from pydantic import BaseModel


class DetectionOut(BaseModel):
    id: int
    label: str
    bbox: list[float]  # [x1, y1, x2, y2] normalized 0-1
    score: float | None
    model: str
    task: str
    detected_at: datetime

    model_config = {"from_attributes": True}


class DetectionJobRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    model: str          # "florence2_large", "florence2_promptgen"
    task: str           # "<OD>", "<CAPTION_TO_PHRASE_GROUNDING>"
    custom_prompt: str = ""        # shared prompt for grounding; ignored when use_caption_as_prompt=True
    use_caption_as_prompt: bool = False   # use each image's stored caption_text as its own prompt
    overwrite: bool = True
