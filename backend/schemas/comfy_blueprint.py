"""The structured-generation blueprint (M6 §2), as saved on `comfy_blueprints.spec`.

Structural checks only: types and bounds. Everything that needs the spec as a whole
(unknown axis names, a label with no folder name, a duplicate path) is the pure
expansion's job (services/blueprint_expand.py), so a draft with mistakes can still be
saved and shown with its errors. Every field is bounded because the spec is stored as
JSON and returned verbatim on every read.
"""
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]


class Entity(BaseModel):
    id: str = Field(max_length=64)
    label: str = Field(default="", max_length=200)
    # Substituted verbatim wherever a template names the entity's axis, which is what
    # keeps one character recognisable across every leaf they appear in.
    descriptor: str = Field(default="", max_length=2000)


class CombineItem(BaseModel):
    axis: Name
    # >1 is itertools.combinations: unordered, distinct ("individually and together").
    take: int = Field(default=1, ge=1, le=4)


class Scenario(BaseModel):
    name: str = Field(max_length=100)
    combine: list[Name | CombineItem] = Field(default_factory=list, max_length=6)
    # "full" or "sample:K".
    mode: str = Field(default="full", max_length=20)
    # Images each leaf should hold.
    count: int = Field(default=10, ge=1, le=10_000)
    path: str = Field(default="", max_length=400)
    # None/"" = join the combined descriptors.
    template: str | None = Field(default=None, max_length=4000)


Piece = Annotated[str, StringConstraints(max_length=300)]


class BlueprintSpec(BaseModel):
    # A dict's max_length bounds its keys; each value list carries its own bound.
    axes: dict[Name, Annotated[list[Entity], Field(max_length=200)]] = Field(
        default_factory=dict, max_length=8
    )
    variations: dict[Name, Annotated[list[Piece], Field(max_length=100)]] = Field(
        default_factory=dict, max_length=12
    )
    scenarios: list[Scenario] = Field(default_factory=list, max_length=50)
