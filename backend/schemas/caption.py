from typing import Literal

from pydantic import BaseModel


class CaptionUpdate(BaseModel):
    caption_text: str
    tags: list[str]
    caption_style: str = ""



class FindReplaceRequest(BaseModel):
    find: str
    replace: str
    use_regex: bool = False
    image_ids: list[str] | None = None


class BulkEditRequest(BaseModel):
    operation: Literal["prepend", "append", "remove", "find_replace"]
    text: str
    replacement: str = ""
    use_regex: bool = False
    image_ids: list[str] | None = None
    quality_flags: list[str] | None = None


class BulkEditResponse(BaseModel):
    affected: int
    skipped: int


class TagStatItem(BaseModel):
    tag: str
    count: int
    category: str


class CaptionOut(BaseModel):
    image_id: str
    caption_text: str
    tags: list[str]
    caption_style: str
    captioned_by: str
