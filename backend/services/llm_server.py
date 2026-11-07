"""Freeing the GPU from a local Strata LLM server before ComfyUI generates.

Strata (an OpenAI-compatible server for MoE models) sizes its expert cache to the whole
card it starts on, so on a 16 GB GPU it and a ComfyUI generation do not fit together.
It can drop its model without stopping: `POST /v1/unload` frees the card in about two
seconds and leaves the server answering, and the next chat request loads the model
again by itself (about 40 s warm, measured on a 16 GB card on 2026-11-06). So the
handoff needs only the unload; the reload is implicit, and a provider's `timeout_s`
must cover it.

Everything here is gated by `ThresholdSettings.llm_gpu_handoff` (off by default): on a
card with room for both, unloading would only cost a reload. Every call is best effort,
returns notes, and never raises — a failure to free the GPU must not fail the job that
asked. A server is recognised by its `GET /health` answering `"service": "strata"`; any
other server (LM Studio, llama.cpp, a remote API) is left alone here.

All requests go through `run_service._LLM_TRANSPORT`, read at call time, so the autouse
test fixture that refuses every LLM-server call covers this module too.
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# /v1/unload?wait=1 lets a request in flight finish first (Strata waits up to 300 s).
# Ours gives up sooner: this runs on the single job worker, and a request in flight here
# can only be another install's, sharing the server. Then the unload is a note and the
# generation goes ahead (docs/dev/comfyui/gpu-handoff.md).
_UNLOAD_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 3.0


def _transport() -> httpx.AsyncBaseTransport | None:
    from backend.services import run_service

    return run_service._LLM_TRANSPORT


def server_root(base_url: str) -> str:
    """A provider's base URL without its trailing `/v1`: where `/health` lives."""
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


async def strata_health(base_url: str, api_key: str = "") -> dict | None:
    """The server's `/health` body if it is Strata, else None (including unreachable)."""
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S, transport=_transport()) as client:
            resp = await client.get(f"{server_root(base_url)}/health", headers=_headers(api_key))
        if resp.status_code != 200:
            return None
        body = resp.json()
    except Exception:
        return None
    return body if isinstance(body, dict) and body.get("service") == "strata" else None


async def unload_strata(base_url: str, api_key: str = "", label: str = "Strata") -> str:
    """Drop Strata's model from the GPU; the server keeps running. Returns a note."""
    try:
        async with httpx.AsyncClient(timeout=_UNLOAD_TIMEOUT_S, transport=_transport()) as client:
            # JSON body required: Strata answers 415 to a bodiless POST.
            resp = await client.post(
                f"{server_root(base_url)}/v1/unload",
                params={"wait": "1"},
                json={},
                headers=_headers(api_key),
            )
            resp.raise_for_status()
        return f"Unloaded {label}'s model from the GPU"
    except Exception as e:
        logger.warning("Strata unload at %s failed: %s", base_url, e)
        return f"{label} did not unload its model ({type(e).__name__}); continuing"


async def handoff_enabled(db: AsyncSession | None = None) -> bool:
    """Whether Settings → ComfyUI's GPU handoff is on. A failed read counts as off."""
    from backend.services.threshold_service import get_thresholds

    try:
        if db is not None:
            return bool((await get_thresholds(db)).llm_gpu_handoff)
        from backend.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            return bool((await get_thresholds(session)).llm_gpu_handoff)
    except Exception:
        logger.warning("Could not read the GPU handoff setting; treating it as off", exc_info=True)
        return False


async def free_local_llm_servers() -> list[str]:
    """Before ComfyUI generates: unload every loaded Strata among the local providers.

    No-op (empty list) while the setting is off. Providers are deduplicated by server,
    since two provider rows can name the same one. Returns one note per server acted on.
    """
    from backend.database import AsyncSessionLocal
    from backend.models.openai_provider import OpenAIProvider
    from backend.schemas.openai_provider import _is_remote

    try:
        async with AsyncSessionLocal() as db:
            if not await handoff_enabled(db):
                return []
            providers = (await db.execute(select(OpenAIProvider))).scalars().all()
            servers = {}
            for p in providers:
                if not _is_remote(p.base_url):
                    servers.setdefault(server_root(p.base_url), (p.name, p.base_url, p.api_key))
    except Exception:
        logger.warning("Could not read the LLM providers for the GPU handoff", exc_info=True)
        return []

    notes = []
    for name, base_url, api_key in servers.values():
        health = await strata_health(base_url, api_key)
        if health is None or not health.get("loaded"):
            continue
        notes.append(await unload_strata(base_url, api_key, label=name))
    return notes
