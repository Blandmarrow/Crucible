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
    "export",
)
StageKey = Literal[
    "prompts", "handoff_to_comfy", "generate", "handoff_to_local", "score", "caption", "export"
]
STAGE_LABELS: dict[str, str] = {
    "prompts": "Fill the blueprint",
    "handoff_to_comfy": "Free VRAM for ComfyUI",
    "generate": "Generate",
    "handoff_to_local": "Free ComfyUI's VRAM",
    "score": "Score",
    "caption": "Caption",
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
    model: str = Field(min_length=1, max_length=300)
    style: str = Field(default="detailed", max_length=100)
    custom_prompt: str = Field(default="", max_length=4000)
    # On by default: a ComfyUI run with `set_caption` has already written each prompt
    # as the caption, so `False` would find nothing to caption. The stage is scoped to
    # this run's own images, so nothing else is touched.
    overwrite: bool = True


class ExportSettings(BaseModel):
    kind: Literal["plain", "kohya", "aitoolkit"] = "plain"
    output_dir: str = Field(min_length=1, max_length=1024)
    n_repeats: int = Field(default=10, ge=1, le=1000)
    concept_token: str = Field(default="concept", max_length=200)
    concept_name: str = Field(default="concept", max_length=200)
    captioned_only: bool = False
    exclude_duplicates: bool = True


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
