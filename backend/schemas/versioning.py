from typing import Any, Literal

from pydantic import BaseModel

from backend.schemas import UtcDatetime


class BranchOut(BaseModel):
    id: str
    dataset_id: str
    name: str
    head_version_id: str | None
    head_version_name: str | None = None
    created_at: UtcDatetime

    model_config = {"from_attributes": True}


class BranchCreate(BaseModel):
    name: str
    from_version_id: str | None = None
    include_snapshot: bool = True


class CheckoutRequest(BaseModel):
    pre_restore_snapshot: bool = True


class VersionOut(BaseModel):
    id: str
    dataset_id: str
    branch_id: str | None
    parent_id: str | None
    name: str | None
    description: str
    image_count: int
    created_at: UtcDatetime
    source: Literal["manual", "pre_restore", "branch_init"]
    is_pinned: bool

    model_config = {"from_attributes": True}


class SnapshotCreate(BaseModel):
    name: str | None = None
    description: str = ""
    branch_id: str | None = None


class VersionUpdate(BaseModel):
    is_pinned: bool | None = None


class RestoreRequest(BaseModel):
    handle_extra_images: str = "keep"
    pre_restore_snapshot: bool = True


class RestoreSummary(BaseModel):
    files_restored: int
    files_unavailable: int
    images_re_created: int
    images_removed: int
    pre_restore_version_id: str | None


class ImageDiffEntry(BaseModel):
    image_id: str | None
    filename: str
    subfolder: str
    caption: str


class ModifiedImageDiff(BaseModel):
    image_id: str | None
    filename: str
    subfolder: str
    changes: dict[str, Any]


class DiffOut(BaseModel):
    added: list[ImageDiffEntry]
    removed: list[ImageDiffEntry]
    modified: list[ModifiedImageDiff]
    unchanged_count: int
    summary: dict[str, int]
