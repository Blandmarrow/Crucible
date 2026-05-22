from sqlalchemy import Float, Integer
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
