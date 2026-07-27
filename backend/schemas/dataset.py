from pydantic import BaseModel, Field, field_validator

from backend.licenses import FIELD_MAX_LEN, normalize_license_input
from backend.schemas import UtcDatetime


class ProvenanceDefaults(BaseModel):
    """Dataset-level provenance defaults. "" means unset; images with a NULL
    field of the same name inherit these (see backend/licenses.py)."""
    source_name: str = Field("", max_length=FIELD_MAX_LEN["source_name"])
    source_url: str = Field("", max_length=FIELD_MAX_LEN["source_url"])
    # No max_length: normalization adds the `other:` prefix after Pydantic would
    # have checked, so the cap has to run after it — see normalize_license_input.
    license: str = ""
    attribution: str = Field("", max_length=FIELD_MAX_LEN["attribution"])

    _norm_license = field_validator("license")(normalize_license_input)


class DatasetCreate(ProvenanceDefaults):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    category: str = ""


class DatasetUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    category: str | None = None
    # None = leave unchanged; "" = clear the default.
    source_name: str | None = Field(None, max_length=FIELD_MAX_LEN["source_name"])
    source_url: str | None = Field(None, max_length=FIELD_MAX_LEN["source_url"])
    license: str | None = None
    attribution: str | None = Field(None, max_length=FIELD_MAX_LEN["attribution"])

    _norm_license = field_validator("license")(normalize_license_input)


class DatasetImport(BaseModel):
    folder_path: str


class DatasetImportWithOptions(ProvenanceDefaults):
    """Import options. The provenance fields apply to every imported image and
    take precedence over sidecar/EXIF capture."""
    folder_path: str
    subfolder: str = ""
    preserve_structure: bool = False
    import_captions: bool = True
    # Opt-in: a video is orders of magnitude larger than the images beside it,
    # so a mixed folder imported into an image dataset must not silently pull
    # gigabytes in. Videos land flat in videos/, ignoring subfolder/preserve_structure.
    include_videos: bool = False


class DatasetRescanRequest(BaseModel):
    import_captions: bool = True


class CaptionImportRequest(BaseModel):
    folder_path: str


class SubfolderInfo(BaseModel):
    path: str
    image_count: int


class SubfolderCreate(BaseModel):
    path: str


class LicenseUsage(BaseModel):
    """One distinct effective license in a dataset. `""` = no license recorded."""
    license: str
    count: int


class DatasetDuplicateRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=255)
    source_version_id: str | None = None  # None = duplicate current on-disk state


class DatasetOut(BaseModel):
    id: str
    name: str
    description: str
    category: str = ""
    folder_path: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    image_count: int
    captioned_count: int
    total_size_bytes: int
    # Videos are counted apart from image_count/total_size_bytes on purpose —
    # see backend/models/dataset.py and docs/dev/video.md.
    video_count: int = 0
    video_size_bytes: int = 0
    preview_image_ids: list[str] = []
    current_branch_id: str | None = None
    source_name: str = ""
    source_url: str = ""
    license: str = ""
    attribution: str = ""

    model_config = {"from_attributes": True}


class DatasetStats(BaseModel):
    id: str
    name: str
    image_count: int
    captioned_count: int
    caption_coverage_pct: float
    total_size_bytes: int
    total_size_mb: float
    avg_width: float | None
    avg_height: float | None
    aspect_ratio_distribution: dict[str, int]
    format_distribution: dict[str, int]
    score_distribution: dict[str, int]
    # Extended distributions
    blur_distribution: dict[str, int] = {}
    noise_distribution: dict[str, int] = {}
    uniformity_distribution: dict[str, int] = {}
    watermark_distribution: dict[str, int] = {}
    color_distribution: dict[str, int] = {}
    saturation_distribution: dict[str, int] = {}
    megapixel_distribution: dict[str, int] = {}
    file_size_distribution: dict[str, int] = {}
    file_size_summary: dict[str, float] = {}
    aspect_ratio_fine: dict[str, int] = {}
    caption_length_distribution: dict[str, int] = {}
    caption_token_distribution: dict[str, int] = {}
    style_similarity_distribution: dict[str, int] = {}
    quality_flag_counts: dict[str, int] = {}
    score_coverage: dict[str, int] = {}
    # Effective license (image value coalesced over the dataset default) → count.
    # "" is the bucket for images with no license recorded anywhere.
    license_breakdown: dict[str, int] = {}


class TagCooccurrence(BaseModel):
    tags: list[str]
    matrix: list[list[int]]
