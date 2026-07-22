from typing import Any
from pydantic import BaseModel, Field

from backend.schemas import UtcDatetime
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
    created_at: UtcDatetime
    aesthetic_score: float | None
    blur_score: float | None
    noise_score: float | None
    uniformity_score: float | None = None
    watermark_score: float | None = None
    nsfw_score: float | None = None
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
    captioned_at: UtcDatetime | None
    is_auto_named: bool = False
    sort_order: int | None = None
    updated_at: UtcDatetime
    detections: list[DetectionOut] = []

    # Raw provenance as stored: NULL/"" means the value is inherited from the
    # dataset. Sent alongside `provenance` (the resolved view) so the UI can
    # distinguish "inherited from dataset" from "overridden on this image".
    source_name: str | None = None
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None
    source_meta: dict | None = None
    # Resolved values + an `inherited` list of field names — see
    # backend.licenses.resolve_provenance. Populated by the router.
    provenance: dict | None = None

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
    captioned_by: str
    is_auto_named: bool = False
    sort_order: int | None = None
    updated_at: UtcDatetime
    # Effective license (own value coalesced over the dataset default), for the
    # gallery badge. Nullable because validation reads the *raw* Image.license,
    # which is NULL whenever the image inherits; the router overwrites it with
    # the resolved value before responding. "" when neither level records one.
    license: str | None = ""

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
    rename_on_move: bool = True


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
    sort_by_sort_order: bool = False


class ImageReorderUpdate(BaseModel):
    id: str
    sort_order: int


class BatchReorderRequest(BaseModel):
    dataset_id: str
    updates: list[ImageReorderUpdate]


class BulkDeleteRequest(BulkFilterBase):
    include_flagged: bool = True


class BulkCountRequest(BulkFilterBase):
    include_flagged: bool = False


# Sentinel distinguishing "don't touch this field" (None) from "clear it so the
# image inherits the dataset default" (INHERIT_SENTINEL). A bare "" cannot carry
# both meanings, and bulk labeling needs to express each of them.
INHERIT_SENTINEL = "__inherit__"


class BulkProvenanceRequest(BulkFilterBase):
    """Set source/license on a selection. Each field: None = leave unchanged,
    "__inherit__" = clear to NULL (inherit the dataset default), any other
    string = set that value.

    Caps mirror the column widths (see ProvenanceDefaults); INHERIT_SENTINEL is
    11 chars and fits under every one of them."""
    source_name: str | None = Field(None, max_length=255)
    source_url: str | None = Field(None, max_length=1024)
    license: str | None = Field(None, max_length=64)
    attribution: str | None = None


class BulkProvenanceResult(BaseModel):
    updated: int


class ImageProvenanceUpdate(BaseModel):
    """Per-image provenance edit; same sentinel semantics and caps as the bulk form."""
    source_name: str | None = Field(None, max_length=255)
    source_url: str | None = Field(None, max_length=1024)
    license: str | None = Field(None, max_length=64)
    attribution: str | None = None


class LicenseOut(BaseModel):
    id: str
    label: str
    allows_commercial: bool | None
    requires_attribution: bool
    share_alike: bool
    url: str = ""
