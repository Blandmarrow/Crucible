"""Request and response models for automated runs (M6 §4, `routers/runs.py`)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# The order stages always run in, whichever are switched on. §5 adds the gate and
# the loop; §6 the snapshot.
STAGE_ORDER: tuple[str, ...] = (
    "prompts",
    "handoff_to_comfy",
    "generate",
    "handoff_to_local",
    "score",
    "caption",
    "free_caption_model",
    "export",
)
StageKey = Literal[
    "prompts", "handoff_to_comfy", "generate", "handoff_to_local", "score", "caption",
    "free_caption_model", "export",
]
STAGE_LABELS: dict[str, str] = {
    "prompts": "Fill the blueprint",
    "handoff_to_comfy": "Free VRAM for ComfyUI",
    "generate": "Generate",
    "handoff_to_local": "Free ComfyUI's VRAM",
    "score": "Score",
    "caption": "Caption",
    "free_caption_model": "Unload the caption model",
    "export": "Export",
}


class GenerateSettings(BaseModel):
    # The base folder a run files its outputs under; each row's own folder nests in it.
    subfolder: str = Field(default="", max_length=512)
    set_caption: bool = True


class ScoreSettings(BaseModel):
    # Technical scoring always runs: it is what flags duplicates (`_flag_duplicates`).
    run_aesthetic: bool = True
    aesthetic_model: Literal["laion", "v2_5"] = "laion"


class CaptionSettings(BaseModel):
    """The caption stage's settings. Every field is named as on
    `routers/captioning.CaptionJobRequest`, which they are passed to as they are
    (`model_dump()`), so the request model's own validators check them. Only the scope
    fields (dataset, ids, subfolder) and the label are the run's to set."""

    model: str = Field(min_length=1, max_length=300)
    style: str = Field(default="detailed", max_length=100)
    custom_prompt: str = Field(default="", max_length=20000)
    # On by default: a ComfyUI run with `set_caption` has already written each prompt
    # as the caption, so `False` would find nothing to caption. The stage is scoped to
    # this run's own images, so nothing else is touched.
    overwrite: bool = True
    target_width: int | None = Field(default=None, ge=1, le=8192)
    target_height: int | None = Field(default=None, ge=1, le=8192)
    strip_refusals: bool = True
    strip_thinking: bool = False
    strip_underscores: bool = False
    strip_hedges: bool = False
    dedupe_tags: bool = False
    save_backup: bool = False
    rename_on_caption: bool = False
    wd14_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    delimiter_mode: Literal["overwrite", "append", "prepend"] = "overwrite"
    delimiter: str = Field(default=", ", max_length=20)
    min_aesthetic_score: float | None = None
    exclude_flags: list[str] | None = None


class ExportSettings(BaseModel):
    """The export stage's settings, named as on the `routers/export` request models.
    `exclude_flags` is a list here and joined for the request, which takes a string.
    Duplicates are left out by default, since the score stage has just flagged them."""

    kind: Literal["plain", "kohya", "aitoolkit"] = "plain"
    output_dir: str = Field(min_length=1, max_length=1024)
    n_repeats: int = Field(default=10, ge=1, le=1000)
    concept_token: str = Field(default="concept", max_length=200)
    concept_name: str = Field(default="concept", max_length=200)
    caption_format: Literal["txt", "caption", "jsonl"] = "txt"
    output_format: str = Field(default="original", max_length=20)
    jpeg_quality: int = Field(default=95, ge=1, le=100)
    resize_to: int | None = Field(default=None, ge=1, le=16384)
    aesthetic_min: float | None = None
    captioned_only: bool = False
    exclude_flags: list[str] = Field(default_factory=lambda: ["is_duplicate"])
    style_sim_min: float | None = None
    strip_metadata: bool = False
    captions_only: bool = False


class RunRecipe(BaseModel):
    """Which stages run, and each one's settings. Stored on `Run.recipe`."""

    stages: list[StageKey] = Field(min_length=1)
    generate: GenerateSettings = GenerateSettings()
    score: ScoreSettings = ScoreSettings()
    caption: CaptionSettings | None = None
    export: ExportSettings | None = None

    @field_validator("stages")
    @classmethod
    def _canonical_order(cls, v: list[str]) -> list[str]:
        return [s for s in STAGE_ORDER if s in set(v)]

    @model_validator(mode="after")
    def _settings_present(self) -> "RunRecipe":
        if "caption" in self.stages and self.caption is None:
            raise ValueError("The caption stage needs caption settings")
        if "export" in self.stages and self.export is None:
            raise ValueError("The export stage needs export settings")
        return self


class RunStartRequest(BaseModel):
    dataset_id: str
    # Required: a run's images are the images its plan rows produce.
    plan_id: str
    name: str = Field(default="", max_length=120)
    recipe: RunRecipe


class RunStage(BaseModel):
    key: str
    status: str
    job_id: str | None = None
    note: str | None = None
    summary: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class RunOut(BaseModel):
    id: str
    dataset_id: str
    plan_id: str | None
    name: str
    recipe: dict
    stages: list[RunStage]
    status: str
    pause_requested: bool
    current_job_id: str | None
    error_msg: str | None
    row_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
