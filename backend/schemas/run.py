"""Request and response models for automated runs (M6 §4, `routers/runs.py`)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# The order stages always run in, whichever are switched on. §6 adds the snapshot.
STAGE_ORDER: tuple[str, ...] = (
    "prompts",
    "rewrite",
    "free_rewrite_model",
    "handoff_to_comfy",
    "generate",
    "handoff_to_local",
    "score",
    "gate",
    "next_pass",
    "caption",
    "free_caption_model",
    "export",
)
# The loop body (M6 §5): these repeat once per pass. `next_pass` ends each pass and is
# never chosen by hand: a run gets one when its recipe has `loop` settings. The tail
# after it (caption, the unload, export) runs once, after the last pass.
BODY_STAGES: tuple[str, ...] = STAGE_ORDER[: STAGE_ORDER.index("next_pass") + 1]
TAIL_STAGES: tuple[str, ...] = STAGE_ORDER[STAGE_ORDER.index("next_pass") + 1:]
StageKey = Literal[
    "prompts", "rewrite", "free_rewrite_model", "handoff_to_comfy", "generate", "handoff_to_local",
    "score", "gate", "caption", "free_caption_model", "export",
]
STAGE_LABELS: dict[str, str] = {
    "prompts": "Fill the blueprint",
    "rewrite": "Rewrite prompts",
    "free_rewrite_model": "Unload the rewrite model",
    "handoff_to_comfy": "Free VRAM for ComfyUI",
    "generate": "Generate",
    "handoff_to_local": "Free ComfyUI's VRAM",
    "score": "Score",
    "gate": "Gate",
    "next_pass": "Decide the next pass",
    "caption": "Caption",
    "free_caption_model": "Unload the caption model",
    "export": "Export",
}
# The flags a gate may test: the ones the score stage writes. Blur, noise, uniformity
# and duplicates come from the technical pass, which always runs; a gate naming the
# watermark or NSFW flag switches that check on in the score stage. Any other flag
# (`has_ai_artifacts`, say) is set by nothing a run does, so it is refused.
GATE_FLAGS: tuple[str, ...] = (
    "is_blurry", "is_noisy", "is_uniform", "is_duplicate", "has_watermark", "is_nsfw",
)


class GenerateSettings(BaseModel):
    # The base folder a run files its outputs under; each row's own folder nests in it.
    subfolder: str = Field(default="", max_length=512)
    set_caption: bool = True


class ScoreSettings(BaseModel):
    # Technical scoring always runs: it is what flags duplicates (`_flag_duplicates`).
    run_aesthetic: bool = True
    aesthetic_model: Literal["laion", "v2_5"] = "laion"


class LoopSettings(BaseModel):
    """The goal loop's hard caps (M6 §5). Required fields, not options: each pass spends
    GPU time, and a stop condition can fail to converge."""

    max_passes: int = Field(ge=1, le=50)
    max_images: int = Field(ge=1, le=100_000)
    # Active minutes: the run's child jobs' running time, so a pause does not count.
    max_minutes: int = Field(ge=1, le=7 * 24 * 60)
    # Calls to a billed LLM provider (rewrite requests plus captioned images). Required
    # when the rewrite or caption stage uses one; checked at the start.
    max_paid_calls: int | None = Field(default=None, ge=1, le=1_000_000)


class GateSettings(BaseModel):
    """What an image must pass to count. A stale score always fails."""

    aesthetic_min: float | None = None
    exclude_flags: list[str] = Field(default_factory=lambda: ["is_duplicate"])

    @field_validator("exclude_flags")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        bad = [f for f in v if f not in GATE_FLAGS]
        if bad:
            raise ValueError(f"The gate cannot test {', '.join(bad)}: no run stage sets it")
        return list(dict.fromkeys(v))


class RewriteSettings(BaseModel):
    """The loop's rewrite stage: `POST …/blueprint/rewrite`'s settings, plus the
    scenarios the user ticked on the start form, which are the review §3 requires."""

    provider_id: str = Field(min_length=1, max_length=36)
    model_name: str = Field(default="", max_length=300)
    batch_size: int = Field(default=5, ge=1, le=10)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    scenarios: list[str] = Field(min_length=1)


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
    Duplicates are left out by default, since the score stage has just flagged them,
    and so is whatever a gate rejected."""

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
    exclude_flags: list[str] = Field(default_factory=lambda: ["is_duplicate", "gate_rejected"])
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
    gate: GateSettings | None = None
    rewrite: RewriteSettings | None = None
    loop: LoopSettings | None = None

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
        if "gate" in self.stages:
            if self.gate is None:
                raise ValueError("The gate stage needs gate settings")
            if "score" not in self.stages:
                raise ValueError("The gate reads scores, so it needs the score stage")
            if self.gate.aesthetic_min is not None and not self.score.run_aesthetic:
                raise ValueError("A minimum aesthetic score needs aesthetic scoring on")
        if "rewrite" in self.stages and self.rewrite is None:
            raise ValueError("The rewrite stage needs rewrite settings")
        if "rewrite" in self.stages and "prompts" not in self.stages:
            raise ValueError("The rewrite stage rewrites the rows the fill adds, so it needs the fill stage")
        if self.loop is not None and not {"prompts", "generate"} <= set(self.stages):
            raise ValueError("Repeating needs the fill and generate stages")
        return self

    @property
    def gate_flags(self) -> set[str]:
        return set(self.gate.exclude_flags) if self.gate and "gate" in self.stages else set()


class RunStartRequest(BaseModel):
    dataset_id: str
    # Required: a run's images are the images its plan rows produce.
    plan_id: str
    name: str = Field(default="", max_length=120)
    recipe: RunRecipe


class RunStage(BaseModel):
    key: str
    status: str
    # Which pass of the loop the entry belongs to; None for a tail stage.
    pass_no: int | None = None
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
    # The loop's ending, `{outcome: "met" | "short", reason}`, once it has one.
    outcome: dict | None = None
    # The loop's folders, short ones first, capped: `{path, target, kept, state, reason}`.
    leaves: list[dict] = Field(default_factory=list)
    leaf_count: int = 0
    met_count: int = 0
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
