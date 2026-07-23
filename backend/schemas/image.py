from typing import Any
from pydantic import BaseModel, Field, field_validator

from backend.licenses import FIELD_MAX_LEN, normalize_license_input
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
    # Deliberately no top-level `source_meta`: it is a scraper's raw payload (up
    # to the 256 KB sidecar cap), it is already carried by `provenance.source_meta`,
    # and this response is refetched on every arrow-key navigation.
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


class ProvenanceEdit(BaseModel):
    """Provenance edit fields: None = leave unchanged, "" = clear to NULL (inherit
    the dataset default), any other string = set that value.

    JSON `null` and `""` already carry the two meanings a provenance edit needs, so
    there is no sentinel string: an earlier `"__inherit__"` sentinel was treated
    exactly like `""` by the router, which meant a source name of literally
    `__inherit__` silently cleared itself.

    Caps mirror the column widths (`licenses.FIELD_MAX_LEN`). `license` is capped by
    a validator instead, because normalization can grow the value — see
    `normalize_license_input`.
    """
    source_name: str | None = Field(None, max_length=FIELD_MAX_LEN["source_name"])
    source_url: str | None = Field(None, max_length=FIELD_MAX_LEN["source_url"])
    license: str | None = None
    attribution: str | None = Field(None, max_length=FIELD_MAX_LEN["attribution"])

    _norm_license = field_validator("license")(normalize_license_input)


class BulkProvenanceRequest(BulkFilterBase, ProvenanceEdit):
    """Set source/license on a selection — see ProvenanceEdit for the field semantics."""
    # False, matching BulkCountRequest and bulk_rename: a bulk op that silently
    # includes images the user has flagged is the surprising default, and the
    # frontend already sends the value explicitly, so nothing changes for it.
    include_flagged: bool = False


class BulkProvenanceResult(BaseModel):
    updated: int


class ImageProvenanceUpdate(ProvenanceEdit):
    """Per-image provenance edit; same semantics and caps as the bulk form."""

