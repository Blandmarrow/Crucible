from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.schemas import UtcDatetime

# Frames one re-extraction run may cover, on *either* scope. The router imports
# this and re-checks it for the `video_id` scope, which resolves its ids from the
# DB rather than the request body — one constant so the two cannot drift, and it
# lives here because a schema must never import from a router.
REEXTRACT_MAX_FRAMES = 5000
# 24 h. Every trim field is a millisecond offset into a video, so this is
# generous by orders of magnitude for real footage; the point is that an
# unbounded `ge=0` int reaches `commit()` and raises `OverflowError: Python int
# too large to convert to SQLite INTEGER` as an unhandled 500.
TRIM_MAX_MS = 86_400_000


class RenameVideoRequest(BaseModel):
    """New filename stem only — the container extension is never user-settable."""

    new_stem: str


class CropRect(BaseModel):
    """`Video.crop_*` column order. Validated against the video's real
    dimensions in the endpoint, which is the only place that knows them."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h


class VideoProbeRequest(BaseModel):
    """Preview parameters for the extraction modal's first step.

    Every bound here is re-enforced server-side in `routers/videos.py`. Pydantic
    caps are a courtesy to the client; the payload budget is a defence of the
    response, and the modal re-probes on every trim-handle drag.
    """

    samples: int = Field(default=8, ge=1, le=12)
    trim_start_ms: int = Field(default=0, ge=0, le=TRIM_MAX_MS)
    trim_end_ms: int = Field(default=0, ge=0, le=TRIM_MAX_MS)
    max_edge: int = Field(default=640, ge=160, le=1280)


class VideoProbeSample(BaseModel):
    timestamp_ms: int
    # A `data:image/jpeg;base64,…` URL, never a temp file path: a file would
    # need a serving endpoint, a cleanup sweep and a traversal guard, all on an
    # unauthenticated server, to hold a preview for the life of a modal.
    data_url: str


class VideoProbeResult(BaseModel):
    samples: list[VideoProbeSample] = []
    # Suggested crop and how much of the sample set agreed on it. NULL crop
    # means "no matte found", which is different from "not looked for".
    crop: CropRect | None = None
    crop_confidence: float = 0.0
    interlace: bool = False
    telecine: bool = False
    # "header" — the container's own frame count; "measured" — this probe seeked
    # for it because the header had none; "unknown" — not seekable, so samples
    # are head-only and the tail trim is unavailable.
    duration_source: Literal["header", "measured", "unknown"] = "header"
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    samples_failed: int = 0
    truncated: bool = False
    warnings: list[str] = []
    # Rides on this response rather than its own route: the modal always probes
    # before offering any control these gate, so a second endpoint would be one
    # more thing to keep in sync for no extra information.
    capabilities: dict = {}


class VideoExtractRequest(BaseModel):
    """One request, one job per video (`video_ids` is a batch, not a merge)."""

    video_ids: list[str] = Field(min_length=1, max_length=50)

    # -- confirmed probe decisions; written to the Video rows by the endpoint --
    crop: CropRect | None = None
    # Not redundant with `crop: None`. Without it, None is ambiguous between
    # "leave the row's stored crop alone" (a batch where only the trims changed)
    # and "the user cleared it" — and a later re-extraction would replay a stale
    # rect forever.
    clear_crop: bool = False
    deinterlace: Literal["", "bwdif"] | None = None
    trim_start_ms: int | None = Field(default=None, ge=0, le=TRIM_MAX_MS)
    trim_end_ms: int | None = Field(default=None, ge=0, le=TRIM_MAX_MS)

    # -- detector --
    sensitivity: float = Field(default=3.0, gt=0, le=100)
    min_shot_ms: int = Field(default=600, ge=0, le=600_000)
    detector_frame_skip: int = Field(default=0, ge=0, le=10)
    max_shots: int = Field(default=5000, ge=1, le=50_000)

    # -- pick --
    frames_per_shot: int = Field(default=1, ge=1, le=20)
    pick: Literal["sharpest", "middle"] = "sharpest"
    candidates: int = Field(default=5, ge=1, le=15)

    # -- output --
    long_edge: int = Field(default=1024, ge=64, le=8192)
    mode: Literal["add", "new_subfolder", "replace"] = "new_subfolder"
    subfolder: str | None = None
    label: str | None = None


class VideoExtractJob(BaseModel):
    video_id: str
    filename: str
    job_id: str
    subfolder: str


class VideoExtractResult(BaseModel):
    jobs: list[VideoExtractJob] = []
    # Videos already covered by a pending or running extraction. They are named
    # rather than silently folded into the batch, and the rest still enqueue.
    skipped: list[dict] = []


class VideoReextractRequest(BaseModel):
    """Pass 2: re-cut already-extracted frames from their source video, full res.

    Exactly one scope, mirroring `UpscaleRunRequest`'s ids-or-subfolder shape:
    `image_ids` for a gallery selection (which can span videos and datasets), or
    `video_id` — optionally narrowed by `subfolder` — for a whole triage batch,
    which is what the extraction-history rows have to hand.
    """

    image_ids: list[str] | None = Field(
        default=None, min_length=1, max_length=REEXTRACT_MAX_FRAMES
    )
    video_id: str | None = None
    subfolder: str | None = None
    # PNG is offered for a lossless capture. It changes the extension, which the
    # job handles as a pure suffix swap — the stem never moves, so the thumbnail
    # and the .txt sidecar stay exactly where they are.
    format: Literal["jpeg", "png"] = "jpeg"
    # None = native resolution, which is the point of pass 2. `render_at_timestamps`
    # already reads 0 as "no downscale".
    max_long_edge: int | None = Field(default=None, ge=64, le=16384)
    label: str | None = None

    @model_validator(mode="after")
    def _exactly_one_scope(self):
        if (self.image_ids is None) == (self.video_id is None):
            raise ValueError("Provide exactly one of image_ids or video_id")
        return self


class VideoReextractGroup(BaseModel):
    """One video's share of a re-extraction — one job each, so one label each."""

    video_id: str
    filename: str
    frames: int
    # Unset on the preview endpoint, which resolves without writing anything.
    job_id: str | None = None


class VideoReextractResult(BaseModel):
    groups: list[VideoReextractGroup] = []
    # {image_id, filename, reason} — every frame the run will not touch, with a
    # reason a user can act on. Preview and enqueue share one resolver, so the
    # modal's accounting and the job's cannot diverge.
    skipped: list[dict] = []
    eligible: int = 0
    total: int = 0


class VideoFramesGroup(BaseModel):
    """One subfolder this video has extracted frames into."""

    # "" is the dataset root, a real group — never render it as "no subfolder".
    subfolder: str
    count: int
    last_extracted_at: UtcDatetime | None = None


class VideoFramesSummary(BaseModel):
    total: int
    groups: list[VideoFramesGroup] = []


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
