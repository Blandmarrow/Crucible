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
    versioning_mode="off",
    auto_rescan_on_open=False,
    comfyui_url="",
)


async def get_thresholds(session: AsyncSession) -> ThresholdSettings:
    row = await session.get(ThresholdSettings, 1)
    return row if row is not None else ThresholdSettings(**DEFAULTS)
