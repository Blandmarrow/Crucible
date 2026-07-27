from pydantic import BaseModel

from backend.schemas import UtcDatetime


class RenameVideoRequest(BaseModel):
    """New filename stem only — the container extension is never user-settable."""

    new_stem: str


class VideoOut(BaseModel):
    id: str
    dataset_id: str
    filename: str
    original_filename: str = ""
    width: int | None
    height: int | None
    file_size_bytes: int | None
    # NULL when the container header could not supply a trustworthy frame count
    # — render "unknown", never 0. See services/video_service.probe_video.
    duration_ms: int | None
    fps: float | None
    # Raw FOURCC as stored, plus the display name derived from it by the router
    # so the frontend does not carry a codec table that can drift.
    codec: str | None
    codec_label: str = ""
    has_poster: bool = False
    created_at: UtcDatetime
    updated_at: UtcDatetime

    # Decode fixups replayed by frame extraction. All-NULL crop means no crop.
    crop_x: int | None = None
    crop_y: int | None = None
    crop_w: int | None = None
    crop_h: int | None = None
    deinterlace: str = ""
    trim_start_ms: int = 0
    trim_end_ms: int = 0

    # Raw provenance as stored: NULL/"" means inherited from the dataset. Sent
    # alongside `provenance` (the resolved view) so the UI can tell "inherited"
    # from "overridden on this video".
    source_name: str | None = None
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None
    provenance: dict | None = None

    model_config = {"from_attributes": True}
