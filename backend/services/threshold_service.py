from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.threshold_settings import ThresholdSettings


DEFAULTS = dict(
    id=1,
    blur_threshold=100.0,
    noise_threshold=15.0,
    uniformity_threshold=12.0,
    duplicate_threshold=8.0,
    watermark_threshold=0.6,
    nsfw_threshold=0.5,
    gdino_threshold=0.35,
    sam3_threshold=0.5,
    versioning_mode="off",
    auto_rescan_on_open=False,
    auto_unload_after_scoring=True,
    comfyui_url="",
    comfy_workflow_dir="",
    # Secrets: "" means "no DB override, inherit the .env/OS-env value". These belong in
    # DEFAULTS even though "" is also the server_default, because get_thresholds builds a
    # *transient* ThresholdSettings(**DEFAULTS) when no row exists, and an unset attribute
    # on a transient object reads None rather than "".
    hf_token="",
    gelbooru_api_key="",
    gelbooru_user_id="",
)


async def get_thresholds(session: AsyncSession) -> ThresholdSettings:
    row = await session.get(ThresholdSettings, 1)
    return row if row is not None else ThresholdSettings(**DEFAULTS)
