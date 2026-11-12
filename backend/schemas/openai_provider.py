from pydantic import BaseModel, Field, field_validator

from backend.schemas import UtcDatetime, mask_secret

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

# The most caption requests a job keeps in flight to one server (M10 T2), for a
# provider's `max_concurrent` and the app-wide Ollama setting alike. Local servers
# rarely run more parallel slots than this, a hosted free tier rate-limits long
# before it, and it bounds what a crash can leave out of the paid-call count
# (decision 16).
MAX_CONCURRENT_REQUESTS = 16


def _is_remote(base_url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        return host.lower() not in _LOCAL_HOSTS
    except Exception:
        return True


def provider_is_billed(row: object) -> bool:
    """Whether calls to this provider count as paid: the explicit `billed` setting,
    else (NULL) whether its URL is remote. Automated runs cap paid calls only."""
    billed = getattr(row, "billed", None)
    return billed if billed is not None else _is_remote(row.base_url)


class OpenAIProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    base_url: str = Field(..., min_length=1, max_length=1024)
    api_key: str = Field(default="", max_length=4096)
    default_model: str = Field(default="", max_length=255)
    # No resolution ceiling — a provider that accepts 4K frames should get them.
    # The bound left is the SQLite integer bind's, so an absurd value is a 422
    # rather than an OverflowError out of the commit. Encoding only ever
    # downscales, so a value above the image's long edge simply sends it as-is.
    max_image_px: int = Field(default=1024, ge=128, le=2**31 - 1)
    # The ceiling is not a real model limit — it keeps an absurd value a 422 rather
    # than an OverflowError out of the SQLite integer bind on commit.
    max_tokens: int = Field(default=2048, ge=1, le=2**31 - 1)
    timeout_s: int = Field(default=300, ge=10, le=3600)
    max_concurrent: int = Field(default=1, ge=1, le=MAX_CONCURRENT_REQUESTS)
    # None = unset, follow the URL (NULL in the column).
    billed: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class OpenAIProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=4096)
    default_model: str | None = Field(default=None, max_length=255)
    # Same ceiling as OpenAIProviderCreate — a 422 instead of an OverflowError.
    max_image_px: int | None = Field(default=None, ge=128, le=2**31 - 1)
    # Same ceiling as OpenAIProviderCreate — a 422 instead of an OverflowError.
    max_tokens: int | None = Field(default=None, ge=1, le=2**31 - 1)
    timeout_s: int | None = Field(default=None, ge=10, le=3600)
    max_concurrent: int | None = Field(default=None, ge=1, le=MAX_CONCURRENT_REQUESTS)
    # None = unchanged (PATCH uses exclude_none), so it can be set true/false but
    # not cleared back to NULL.
    billed: bool | None = None

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
    max_concurrent: int
    is_remote: bool
    billed: bool | None
    is_billed: bool
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
            max_concurrent=getattr(row, "max_concurrent", 1),
            is_remote=_is_remote(row.base_url),
            billed=getattr(row, "billed", None),
            is_billed=provider_is_billed(row),
            created_at=row.created_at,
        )
