from pydantic import BaseModel, Field, field_validator

from backend.schemas import UtcDatetime, mask_secret
from backend.schemas.chat import PRIVACY_CATEGORIES, RESERVED_CATEGORIES, check_privacy

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


def default_privacy(billed: bool) -> dict[str, bool]:
    """The analyst's defaults when nothing is stored: a billed provider gets numbers
    only, a free one every category. Reserved categories are always off."""
    return {
        c: (c not in RESERVED_CATEGORIES) and (not billed or c == "numbers")
        for c in PRIVACY_CATEGORIES
    }


def provider_privacy(row: object) -> dict[str, bool]:
    """What the analyst may send this provider: its stored override over the defaults
    `provider_is_billed` picks. Flipping `billed` changes the defaults with no migration.
    `analyst_context.effective_privacy` lays a chat's own override on top of this."""
    out = default_privacy(provider_is_billed(row))
    out.update(getattr(row, "analyst_privacy", None) or {})
    for c in RESERVED_CATEGORIES:
        out[c] = False
    return out


def tools_ok(row: object, model: str | None) -> bool:
    """Whether the analyst may offer `model` on this provider tools: only when that model
    passed the tool-call probe. Never probed, or failed, means the chat runs without
    tools, as in phase 2. See services/tool_probe.py."""
    if not model:
        return False
    entry = _probe_dict(row).get(model)
    return bool(isinstance(entry, dict) and entry.get("ok") is True)


def _probe_dict(row: object) -> dict:
    """The stored verdicts, or {} when the column holds anything but a dict."""
    stored = getattr(row, "tools_probe", None)
    return stored if isinstance(stored, dict) else {}


def _probe_entries(row: object) -> dict[str, "ToolProbeEntry"]:
    """The stored verdicts that validate. A malformed one (a hand edit, or an older
    shape) is skipped, never a 500 on every provider listing."""
    out = {}
    for model, entry in _probe_dict(row).items():
        try:
            out[model] = ToolProbeEntry.model_validate(entry)
        except Exception:  # noqa: BLE001 — skip what does not validate
            continue
    return out


class ToolProbeEntry(BaseModel):
    ok: bool
    checked_at: str
    detail: str


class ToolProbeRequest(BaseModel):
    # None probes the provider's default model.
    model: str | None = Field(default=None, max_length=255)


class ToolProbeOut(ToolProbeEntry):
    model: str


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
    # None = follow `billed` (NULL in the column); see OpenAIProvider.analyst_privacy.
    analyst_privacy: dict[str, bool] | None = None

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("analyst_privacy")
    @classmethod
    def _check_privacy(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        return check_privacy(v)


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
    # None = unchanged, like `billed`; `analyst_privacy_reset` is how it goes back to
    # NULL (follow `billed`). The router pops the flag before its setattr loop.
    analyst_privacy: dict[str, bool] | None = None
    analyst_privacy_reset: bool = False

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v

    @field_validator("analyst_privacy")
    @classmethod
    def _check_privacy(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        return check_privacy(v)


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
    # The stored override (None = follow `is_billed`) and what is in force either way.
    analyst_privacy: dict[str, bool] | None = None
    analyst_privacy_effective: dict[str, bool]
    # The tool-call probe's verdict per model; see OpenAIProvider.tools_probe.
    tools_probe: dict[str, ToolProbeEntry] = Field(default_factory=dict)
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
            analyst_privacy=getattr(row, "analyst_privacy", None),
            analyst_privacy_effective=provider_privacy(row),
            tools_probe=_probe_entries(row),
            created_at=row.created_at,
        )
