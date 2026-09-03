from pydantic import BaseModel, Field, field_validator

from backend.schemas import UtcDatetime, mask_secret

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
    # The ceiling is not a real model limit — it keeps an absurd value a 422 rather
    # than an OverflowError out of the SQLite integer bind on commit.
    max_tokens: int = Field(default=2048, ge=1, le=2**31 - 1)
    timeout_s: int = Field(default=300, ge=10, le=3600)

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
    # Same ceiling as OpenAIProviderCreate — a 422 instead of an OverflowError.
    max_tokens: int | None = Field(default=None, ge=1, le=2**31 - 1)
    timeout_s: int | None = Field(default=None, ge=10, le=3600)

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
    timeout_s: int
    is_remote: bool
    created_at: UtcDatetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_row(cls, row: object) -> "OpenAIProviderOut":
        # The remaining hand-maintained field list: a column added to the model and
        # to this schema but forgotten here fails *silently* (the field keeps its
        # default). Update this list when you add one.
        masked = mask_secret(getattr(row, "api_key", ""))
        return cls(
            id=row.id,
            name=row.name,
            base_url=row.base_url,
            api_key_masked=masked,
            default_model=row.default_model,
            max_image_px=row.max_image_px,
            max_tokens=getattr(row, "max_tokens", 2048),
            timeout_s=getattr(row, "timeout_s", 300),
            is_remote=_is_remote(row.base_url),
            created_at=row.created_at,
        )
