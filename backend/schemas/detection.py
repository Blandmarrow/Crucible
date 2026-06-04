from datetime import datetime

from pydantic import BaseModel


class DetectionOut(BaseModel):
    id: int
    label: str
    bbox: list[float]  # [x1, y1, x2, y2] normalized 0-1
    score: float | None
    model: str
    task: str
    mask: str | None = None
    detected_at: datetime

    model_config = {"from_attributes": True}


class DetectionJobRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    model: str          # "florence2_large", "florence2_promptgen", "nudenet", "sam2"
    task: str           # "<OD>", "<CAPTION_TO_PHRASE_GROUNDING>", "nudenet", "text_prompt", "points"
    custom_prompt: str = ""        # shared prompt for grounding; ignored when use_caption_as_prompt=True
    use_caption_as_prompt: bool = False   # use each image's stored caption_text as its own prompt
    overwrite: bool = True
    label: str | None = None
    min_prob: float = 0.5          # NudeNet: minimum detection confidence (0-1)
    point_prompts: list[list[float]] | None = None   # SAM2 points mode: [[x,y], ...] normalized 0-1
    point_labels: list[int] | None = None            # SAM2 points mode: 1=foreground, 0=background
