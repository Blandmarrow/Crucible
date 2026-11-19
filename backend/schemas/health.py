"""The health report and dataset notes (M12 phase 1). See docs/dev/analyst/health.md."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas import UtcDatetime

Severity = Literal["error", "warn", "info"]


class FilterSpec(BaseModel):
    """The images a finding is about, as a subset of `ImageFilterParams`.

    Typed and closed (`extra="forbid"`), never a URL or a raw string: the Health page
    sends it to `GET /images/` as it is, and phase 2's buttons and phase 3's tools take
    the same object. Every field must exist on `ImageFilterParams` with the same
    meaning; `backend/tests/test_health_findings.py` checks that.
    """

    model_config = ConfigDict(extra="forbid")

    captioned: bool | None = None
    search: str | None = None
    caption_tokens_min: int | None = None
    caption_tokens_max: int | None = None
    short_side_max: int | None = None
    quality_flag: str | None = None
    score_field: str | None = None
    score_is_null: bool | None = None
    max_score_lt: float | None = None
    scores_stale: bool | None = None
    license_missing: bool | None = None


class FindingFix(BaseModel):
    """The existing tool that fixes a finding: a dataset page (a pane `PageType`) and
    the button text."""

    page: str
    label: str


class Finding(BaseModel):
    id: str
    severity: Severity
    title: str
    # What `count` and `total` count: images, distinct tags, or duplicate groups.
    unit: Literal["images", "tags", "groups"] = "images"
    count: int
    total: int
    numbers: dict = Field(default_factory=dict)
    # None when no single filter selects the affected images (tags used once, spread
    # aspect ratios); the finding then has no "Show images".
    filter_spec: FilterSpec | None = None
    fix: FindingFix | None = None
    # Coarse state, stored with a dismissal so the report can tell when it got worse.
    fingerprint: dict = Field(default_factory=dict)
    # The count in the current branch head's snapshot; None when there is no baseline.
    # 0 when the baseline exists and did not have this finding.
    was: int | None = None
    # Set on a dismissed finding, and on one that came back because it got worse.
    dismissal: "DismissalOut | None" = None
    resurfaced: bool = False


class DismissalOut(BaseModel):
    note_id: str
    reason: str
    permanent: bool
    dismissed_at: UtcDatetime
    # The count when it was dismissed.
    count_then: int | None = None


class Baseline(BaseModel):
    version_id: str
    version_name: str | None
    created_at: UtcDatetime
    # True for a snapshot taken before findings were recorded, or whose report failed.
    missing: bool = False


class ResolvedFinding(BaseModel):
    """A finding the baseline had and the current report does not."""

    id: str
    severity: Severity
    was: int


class HealthSummary(BaseModel):
    error: int = 0
    warn: int = 0
    info: int = 0


class HealthReport(BaseModel):
    dataset_id: str
    findings: list[Finding]
    dismissed: list[Finding]
    resolved: list[ResolvedFinding] = Field(default_factory=list)
    summary: HealthSummary
    baseline: Baseline | None = None
    computed_at: UtcDatetime


class DismissRequest(BaseModel):
    finding_id: str = Field(..., min_length=1, max_length=64)
    reason: str = Field("", max_length=500)
    permanent: bool = False


# --- Notes -------------------------------------------------------------------

DatasetNoteSection = Literal["preference", "decision", "open_item"]
AppNoteSection = Literal["about_me"]


class NoteItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    section: str
    text: str
    position: int
    decided_at: UtcDatetime | None = None
    finding_id: str | None = None
    permanent: bool = False
    # The chat whose accepted proposal added it; null when the user wrote it.
    source_chat_id: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    deleted_at: UtcDatetime | None = None


class NotesOut(BaseModel):
    items: list[NoteItemOut]
    used_chars: int
    cap_chars: int


class DatasetNoteCreate(BaseModel):
    section: DatasetNoteSection
    text: str = Field(..., min_length=1, max_length=2000)
    decided_at: datetime | None = None


class AppNoteCreate(BaseModel):
    section: AppNoteSection = "about_me"
    text: str = Field(..., min_length=1, max_length=2000)


class NoteUpdate(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=2000)
    position: int | None = Field(None, ge=0)
    # Absent = leave unchanged; an explicit null clears it (read via model_fields_set).
    decided_at: datetime | None = None


Finding.model_rebuild()
