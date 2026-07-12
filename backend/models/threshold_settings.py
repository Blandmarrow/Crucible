from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ThresholdSettings(Base):
    __tablename__ = "threshold_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blur_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="100.0")
    noise_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="15.0")
    uniformity_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="12.0")
    duplicate_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="8.0")
    watermark_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.6")
    nsfw_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    gdino_threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.35")
    versioning_mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="'off'")
    auto_rescan_on_open: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    comfyui_url: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
