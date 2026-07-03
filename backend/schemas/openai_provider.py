from pydantic import BaseModel, Field, field_validator

from backend.schemas import UtcDatetime

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _is_remote(base_url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        return host.lower() not in _LOCAL_HOSTS
    except Exception:
        return True


class OpenAIProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    base_url: str = Field(..., min_length=1, max_length=1024)
    api_key: str = Field(default="", max_length=4096)
    default_model: str = Field(default="", max_length=255)
    max_image_px: int = Field(default=1024, ge=128, le=4096)
    max_tokens: int = Field(default=2048, ge=64, le=32768)

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class OpenAIProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=4096)
    default_model: str | None = Field(default=None, max_length=255)
    max_image_px: int | None = Field(default=None, ge=128, le=4096)
    max_tokens: int | None = Field(default=None, ge=64, le=32768)

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v


class OpenAIProviderOut(BaseModel):
    id: str
    name: str
    base_url: str
    api_key_masked: str
    default_model: str
    max_image_px: int
    max_tokens: int
    is_remote: bool
    created_at: UtcDatetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_row(cls, row: object) -> "OpenAIProviderOut":
        key: str = getattr(row, "api_key", "") or ""
        masked = ("*" * max(0, len(key) - 4) + key[-4:]) if len(key) > 4 else ("*" * len(key))
        return cls(
            id=row.id,
            name=row.name,
            base_url=row.base_url,
            api_key_masked=masked,
            default_model=row.default_model,
            max_image_px=row.max_image_px,
            max_tokens=getattr(row, "max_tokens", 2048),
            is_remote=_is_remote(row.base_url),
            created_at=row.created_at,
        )
