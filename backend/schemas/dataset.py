from datetime import datetime
from pydantic import BaseModel, Field


class DatasetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""


class DatasetUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None


class DatasetImport(BaseModel):
    folder_path: str


class DatasetImportWithOptions(BaseModel):
    folder_path: str
    subfolder: str = ""
    preserve_structure: bool = False


class SubfolderInfo(BaseModel):
    path: str
    image_count: int


class SubfolderCreate(BaseModel):
    path: str


class DatasetOut(BaseModel):
    id: str
    name: str
    description: str
    folder_path: str
    created_at: datetime
    updated_at: datetime
    image_count: int
    captioned_count: int
    total_size_bytes: int
    preview_image_ids: list[str] = []
    current_branch_id: str | None = None

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


class TagCooccurrence(BaseModel):
    tags: list[str]
    matrix: list[list[int]]
