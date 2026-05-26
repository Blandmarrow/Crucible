from datetime import datetime
from typing import Any
from pydantic import BaseModel

from backend.schemas.detection import DetectionOut


class ImageOut(BaseModel):
    id: str
    dataset_id: str
    filename: str
    original_filename: str
    subfolder: str = ""
    width: int | None
    height: int | None
    file_size_bytes: int | None
    format: str | None
    phash: str | None
    created_at: datetime
    aesthetic_score: float | None
    blur_score: float | None
    noise_score: float | None
    uniformity_score: float | None = None
    watermark_score: float | None = None
    color_score: float | None = None
    saturation_score: float | None = None
    style_similarity_score: float | None = None
    dino_layer_scores: dict | None = None
    has_dino_layer_embeddings: bool = False
    quality_flags: dict[str, Any]
    generation_metadata: dict | None = None
    caption_text: str
    caption_style: str
    captioned_by: str
    captioned_at: datetime | None
    is_auto_named: bool = False
    updated_at: datetime
    tags_json: list[str]
    detections: list[DetectionOut] = []

    model_config = {"from_attributes": True}


class ImageListItem(BaseModel):
    id: str
    dataset_id: str
    filename: str
    subfolder: str = ""
    width: int | None
    height: int | None
    file_size_bytes: int | None
    format: str | None
    aesthetic_score: float | None
    blur_score: float | None
    uniformity_score: float | None = None
    watermark_score: float | None = None
    color_score: float | None = None
    saturation_score: float | None = None
    style_similarity_score: float | None = None
    dino_layer_scores: dict | None = None
    quality_flags: dict[str, Any]
    generation_metadata: dict | None = None
    caption_text: str
    tags_json: list[str]
    captioned_by: str
    is_auto_named: bool = False
    updated_at: datetime

    model_config = {"from_attributes": True}


class RenameImageRequest(BaseModel):
    new_stem: str


class ImageResizeRequest(BaseModel):
    width: int | None = None
    height: int | None = None
    scale: float | None = None
    maintain_ar: bool = True
    resample: str = "LANCZOS"


class ImageCropRequest(BaseModel):
    x: int
    y: int
    width: int
    height: int
    output_width: int | None = None
    output_height: int | None = None
    replace: bool = False
    upscale_model: str | None = None
    upscale_target_width: int | None = None
    upscale_target_height: int | None = None


class BatchResizeRequest(BaseModel):
    image_ids: list[str]
    width: int | None = None
    height: int | None = None
    scale: float | None = None
    maintain_ar: bool = True


class BatchCropRequest(BaseModel):
    image_ids: list[str]
    target_ar: float
    strategy: str = "center"


class BatchMoveSubfolderRequest(BaseModel):
    image_ids: list[str]
    subfolder: str


class BatchMoveDatasetRequest(BaseModel):
    image_ids: list[str] = []
    source_dataset_id: str | None = None
    source_subfolder: str | None = None
    target_dataset_id: str
    subfolder: str = ""


class BatchCopyDatasetResult(BaseModel):
    copied: int
    target_dataset_id: str


class BulkFilterBase(BaseModel):
    dataset_id: str
    image_ids: list[str] | None = None
    quality_flags: list[str] | None = None
    subfolder: str | None = None


class BulkRenameRequest(BulkFilterBase):
    new_stem: str


class BulkDeleteRequest(BulkFilterBase):
    pass


class BulkCountRequest(BulkFilterBase):
    pass
