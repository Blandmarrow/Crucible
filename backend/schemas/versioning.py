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
    files_failed: int = 0
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


class RatingImpact(BaseModel):
    """What restoring a version would do to the keep/cut ratings on disk now.

    Genuinely a version-vs-*current* comparison, which nothing else here does —
    `diff_versions` reads `VersionImageState` on both sides. A restore reverts a
    rating like any other mirrored column, and a rating is hand-made work that no
    job can recompute, so the confirm dialog says how much of it is at stake.
    """
    # Live images whose rating the restore would change (in either direction).
    will_change: int
    # The subset it would *clear* — a rating given after the snapshot was taken.
    will_clear: int
    # Rated images the version does not contain. Under handle_extra_images="remove"
    # these rows are **deleted**, not reverted, so the modal counts them apart:
    # which of the two numbers applies depends on the mode the user has picked.
    extras_rated: int
