from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

CategoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class PresetCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    model: str = Field(default="", max_length=500)
    style: str = Field(default="", max_length=100)
    prompt: str = Field(default="", max_length=100_000)


class CategoryCreate(BaseModel):
    name: CategoryName


class CategoryRename(BaseModel):
    old_name: CategoryName
    new_name: CategoryName


class CaptioningWorkflowPut(BaseModel):
    # A dict and never None: clearing the value is DELETE, because NULL means "never saved".
    value: dict[str, Any]


class ComfyGenSettings(BaseModel):
    """A plan's "Generate prompts" form. The bounds mirror GeneratePromptsJobRequest,
    so what is saved here is also what a run would accept."""

    instructions: str = Field(default="", max_length=20000)
    provider_id: str = Field(default="", max_length=36)
    model: str = Field(default="", max_length=500)
    batch_size: int = Field(default=5, ge=1, le=10)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    target_count: int = Field(default=20, ge=1, le=200)


class LegacyImportRequest(BaseModel):
    """What one browser still holds in its own storage. A section left out is not
    touched. Entries are typed `Any` on purpose: each is validated on its own, so one
    bad entry is reported rather than rejecting the whole body with a 422."""

    presets: list[Any] | None = None
    categories: list[Any] | None = None
    captioning_workflow: Any = None
    comfy_gen_settings: dict[str, Any] | None = None
