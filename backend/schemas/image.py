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
    luminance_score: float | None = None
    style_similarity_score: float | None = None
    # True when the pixels were rewritten in place after the scores above were
    # measured — see `backend/utils.py::record_in_place`.
    scores_stale: bool = False
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

    # Frame lineage. Set only on images produced by video frame extraction;
    # `source_video_id` goes NULL when the source video is deleted, while the
    # timestamp and shot index survive it.
    source_video_id: str | None = None
    source_timestamp_ms: int | None = None
    source_shot_index: int | None = None

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
    luminance_score: float | None = None
    style_similarity_score: float | None = None
    # True when the pixels were rewritten in place after the scores above were
    # measured — drives the gallery card's warning badge.
    scores_stale: bool = False
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
    # Lineage marker only — the gallery card needs no timestamp or shot index,
    # and this payload is paid per row on every page.
    source_video_id: str | None = None

    model_config = {"from_attributes": True}


class ImageFilterParams(BaseModel):
    """Every filter `GET /images/` accepts — `dataset_id` plus the whole tail —
    minus paging (`page`/`limit`) and ordering (`sort`/`order`).

    One declaration shared by the three endpoints that have to agree on what
    "the current view" means: `GET /images/` (the grid), `GET /images/count`
    (the total behind the pagination row and the select-all offer) and
    `GET /images/ids` (what select-all actually grabs). A filter added here
    reaches all three at once; a filter added to one signature only is exactly
    the drift that makes select-all quietly disagree with the grid it was
    offered from. `backend/tests/test_image_select_all_scope.py` asserts the
    three param sets still match, so that drift fails CI.

    Deliberately **not** `extra="forbid"`: unknown query params have always been
    ignored on this route, and forbidding them would turn a stale bookmarked
    gallery URL — or a param added to the frontend ahead of the backend — into a
    422 across all three endpoints at once.
    """
    dataset_id: str
    captioned: bool | None = None
    search: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    score_field: str | None = None
    score_is_null: bool | None = None
    quality_flag: str | None = None
    file_size_min: int | None = None
    file_size_max: int | None = None
    mp_min: float | None = None
    mp_max: float | None = None
    ar_min: float | None = None
    ar_max: float | None = None
    format_filter: str | None = None
    score_filters: str | None = None
    subfolder: str | None = None
    source_video_id: str | None = None
    detection_label: str | None = None
    detection_label_exact: str | None = None
    detection_score_min: float | None = None
    detection_score_max: float | None = None
    detection_score_null: bool | None = None
    mask_coverage_min: float | None = None
    mask_coverage_max: float | None = None
    detection_count_min: int | None = None
    detection_count_max: int | None = None
    caption_words_min: int | None = None
    caption_words_max: int | None = None
    caption_tokens_min: int | None = None
    caption_tokens_max: int | None = None
    license_filter: str | None = Field(
        None, description="JSON array of effective license ids; empty = no filter"
    )
    license_missing: bool | None = None


class ImageIdsParams(ImageFilterParams):
    """The filters plus the grid's ordering — `GET /images/ids`.

    Paging and ordering are separate models rather than fields on
    `ImageFilterParams` because only the filters define *which* images the view
    means; `/count` must not care how they are ordered. Subclassing rather than
    a second flat declaration is what keeps the three param sets provably
    nested.

    They are models at all — rather than plain function args beside
    `Annotated[ImageFilterParams, Query()]` — because FastAPI unpacks a Pydantic
    query model only when it is the route's *sole* query parameter
    (`dependencies/utils.py::request_params_to_args`, the `len(fields) == 1`
    branch). One scalar `sort: str = "created_at"` beside it silently turns the
    model back into a single required query param literally named `f`.
    """
    sort: str = "created_at"
    order: str = "desc"


class ImageListParams(ImageIdsParams):
    """The filters, the ordering and one page of them — `GET /images/`."""
    page: int = Field(1, ge=1)
    limit: int = Field(50, ge=1, le=500)


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
    label: str | None = None


class BatchCropRequest(BaseModel):
    image_ids: list[str]
    target_ar: float
    strategy: str = "center"
    label: str | None = None


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


class BulkThumbnailRequest(BulkFilterBase):
    """Re-cut the thumbnails of a scope whose previews have gone stale.

    The repair for the four jobs that regenerate a thumbnail as a best-effort
    post-commit epilogue (batch LUT, batch upscale, crop+upscale, video
    re-extract): each reports a `thumbnails_stale` count, and this rebuilds them.
    A **scope**, never the affected id list — the trigger (a full volume, a
    read-only `thumbnails/`) is deterministic, so "the affected images" is
    "everything the run touched", and over-scoping costs one re-encode per image.
    """
    include_flagged: bool = False
    label: str | None = None


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

