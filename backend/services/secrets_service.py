"""Resolve the three DB-backed secrets, and project HF_TOKEN into the process environment.

Three stores are involved, and each has exactly one job:

* **SQLite** (``threshold_settings.hf_token`` / ``gelbooru_api_key`` / ``gelbooru_user_id``)
  is the durable, user-editable store. ``""`` means "no override, inherit".
* **``backend.config.settings``** is the immutable record of the ``.env`` / OS-env chain as
  it stood at import. It is the fallback, and the restore target when a DB override is
  cleared.
* **``os.environ["HF_TOKEN"]``** is the runtime *projection* — a derived value, never a
  source of truth, written only by :func:`sync_env` below.

Two things here look wrong to a reader and are not:

**Nothing may ever assign to ``settings.*``.** The singleton is a plain mutable pydantic
instance (``config.py`` has no ``frozen=True`` and no ``lru_cache``, and the test suite
monkeypatches it), so "immutable" is a chosen invariant rather than something the type
enforces. It holds because a cleared override has to fall back to what ``.env`` said, and
the only surviving record of that is the singleton. Write the DB, not the singleton.

**Why a runtime env projection is needed at all.** Eight of the nine HuggingFace loaders in
this codebase pass no ``token=`` and rely on the ambient ``HF_TOKEN`` variable
(``aesthetic_scorer``, ``wd14_tagger``, ``sam2_predictor``, and model_manager's Florence-2 /
LLaVA / DINOv2 / NSFW loaders). ``_load_paligemma2_sync`` is sync and runs in an executor
thread, so it cannot await an ``AsyncSession`` to read the DB itself. The env var is
therefore the de-facto carrier, and this assignment is load-bearing: without it a token
saved in the DB would reach exactly one loader. ``huggingface_hub``'s
``_get_token_from_environment()`` re-reads ``os.environ`` on every call and caches nothing,
so a mid-process assignment reaches every subsequent download with no restart.
"""

import os

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.threshold_settings import ThresholdSettings
from backend.services.threshold_service import get_thresholds

# The DB column names, which are also the backend.config.Settings field names — one string
# indexes both stores. Order is the display order in Settings -> API Keys.
SECRET_FIELDS = ("hf_token", "gelbooru_api_key", "gelbooru_user_id")

# Only hf_token has ambient consumers that read the process environment directly; the two
# gelbooru credentials are passed explicitly by routers/booru.py and get no projection.
_ENV_VARS = {"hf_token": "HF_TOKEN"}


def resolve_secret(row: ThresholdSettings | None, field: str) -> str:
    """The effective value of ``field``: the DB override when non-empty, else the env chain.

    ``row=None`` is legal and means "no DB row yet" — it resolves purely to ``settings``.
    """
    return (getattr(row, field, "") or "") or getattr(settings, field)


def secret_source(row: ThresholdSettings | None, field: str) -> str:
    """Where the effective value came from: ``"db"``, ``"env"`` or ``"unset"``."""
    if getattr(row, field, "") or "":
        return "db"
    return "env" if getattr(settings, field) else "unset"


def sync_env(row: ThresholdSettings | None) -> None:
    """Project the effective secrets into the process environment. The only writer.

    Assignment, never ``setdefault``: the whole point is that saving a token in the UI takes
    effect for the *next* download without a restart, and ``setdefault`` would silently keep
    whatever was there first.

    Clearing pops the variable rather than assigning ``""``, because popping is what
    re-exposes ``HUGGING_FACE_HUB_TOKEN`` and ``~/.cache/huggingface/token`` to
    huggingface_hub's lookup, and what leaves process state indistinguishable from a fresh
    boot — which is the testable definition of "restored". Note the pop branch cannot remove
    a token the OS set: if the OS environment carried ``HF_TOKEN`` at import, pydantic read
    it into ``settings.hf_token``, so :func:`resolve_secret` is non-empty and the assignment
    branch fires instead. The pop is reachable only when the variable was absent or empty at
    import time.
    """
    for field, var in _ENV_VARS.items():
        value = resolve_secret(row, field)
        if value:
            os.environ[var] = value
        else:
            os.environ.pop(var, None)


async def sync_env_from_db(session: AsyncSession) -> None:
    """Read the settings row and project it. Called at startup, once the DB exists."""
    sync_env(await get_thresholds(session))
