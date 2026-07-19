from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.schemas import UtcDatetime


class DetectionOut(BaseModel):
    id: int
    label: str
    bbox: list[float]  # [x1, y1, x2, y2] normalized 0-1
    score: float | None
    model: str
    task: str
    mask: str | None = None
    detected_at: UtcDatetime

    model_config = {"from_attributes": True}


class DetectionJobRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    subfolder: str | None = None
    quality_flags: list[str] | None = None   # exclude images with these flags set
    model: str          # "florence2_large", "florence2_promptgen", "nudenet", "sam2"
    task: str           # "<OD>", "<CAPTION_TO_PHRASE_GROUNDING>", "nudenet", "text_prompt", "points"
    custom_prompt: str = ""        # shared prompt for grounding; ignored when use_caption_as_prompt=True
    use_caption_as_prompt: bool = False   # use each image's stored caption_text as its own prompt
    overwrite: bool = True
    label: str | None = None
    min_prob: float = 0.5          # NudeNet: minimum detection confidence (0-1)
    point_prompts: list[list[float]] | None = None   # SAM2 points mode: [[x,y], ...] normalized 0-1
    point_labels: list[int] | None = None            # SAM2 points mode: 1=foreground, 0=background
    sync_watermark_flag: bool = False    # set/clear Image.has_watermark from per-image results


class DetectionUpdate(BaseModel):
    label: str = Field(min_length=1, max_length=256)


class DetectionBulkDeleteRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None       # None = dataset scope
    subfolder: str | None = None
    quality_flags: list[str] | None = None   # exclude images with these flags set
    labels: list[str] | None = None          # restrict to these detection labels
    models: list[str] | None = None          # restrict to these detection models
    score_below: float | None = Field(None, ge=0.0, le=1.0)  # delete score < this (NULL never matches)
    dry_run: bool = False


class DetectionMergeRequest(BaseModel):
    detection_ids: list[int] = Field(min_length=2)


class ManualDetectionRequest(BaseModel):
    image_id: str
    bbox: list[float] = Field(min_length=4, max_length=4)  # [x1, y1, x2, y2] normalized 0-1
    label: str = Field(min_length=1, max_length=256)
    refine_with_sam: bool = False


class DetectionRefineRequest(BaseModel):
    point_prompts: list[list[float]] = Field(min_length=1)  # [[x,y], ...] normalized 0-1
    point_labels: list[int]                                 # 1=foreground, 0=background

    @model_validator(mode="after")
    def _lengths_match(self):
        if len(self.point_prompts) != len(self.point_labels):
            raise ValueError("point_prompts and point_labels must have equal length")
        return self


class DetectionCropRequest(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None       # None = dataset scope
    subfolder: str | None = None
    quality_flags: list[str] | None = None   # exclude images with these flags set
    labels: list[str] | None = None          # detection labels to crop to; None/[] = all
    mode: Literal["union", "largest"] = "union"
    padding_pct: float = Field(0.0, ge=0.0, le=100.0)
    target_ar: float | None = Field(None, gt=0.0)    # width/height; grow-only snap
    replace: bool = False
    label: str | None = None                 # job-label override (NOT a detection label)
