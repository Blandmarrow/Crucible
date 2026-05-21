import logging
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Paths
    base_dir: Path = Path(__file__).parent.parent
    data_dir: Path = Path(__file__).parent.parent / "data"
    datasets_dir: Path = Path(__file__).parent.parent / "data" / "datasets"
    models_cache_dir: Path = Path(__file__).parent.parent / "models_cache"
    upscale_models_dir: Path = Path(__file__).parent.parent / "models" / "upscale_models"

    # Database (absolute path so it's consistent regardless of working directory)
    database_url: str = f"sqlite+aiosqlite:///{Path(__file__).parent.parent / 'dataset_manager.db'}"

    # HuggingFace
    hf_token: str = ""

    # Booru APIs
    gelbooru_api_key: str = ""
    gelbooru_user_id: str = ""

    # ML settings
    max_vram_mb: int = 20000
    ollama_base_url: str = "http://localhost:11434"
    ollama_image_max_px: int = 1024  # resize images before sending to Ollama

    # Scoring thresholds (exposed here so they can be tweaked via .env without code changes)
    watermark_threshold: float = 0.6

    # Thumbnail settings
    thumbnail_size: int = 256

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.max_vram_mb < 1000:
            raise ValueError(
                f"max_vram_mb={self.max_vram_mb} is too low — models require at least 1 GB. "
                "Set MAX_VRAM_MB to 1000 or higher in .env."
            )
        if not self.hf_token:
            logger.debug(
                "HF_TOKEN is not set. PaliGemma-2 downloads will fail. "
                "Set HF_TOKEN in .env if you plan to use PaliGemma-2."
            )
        return self

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.models_cache_dir.mkdir(parents=True, exist_ok=True)
        self.upscale_models_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
