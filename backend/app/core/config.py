import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_runners() -> int:
    """預設 runner 數：CPU 核心數的一半，最少 1。"""
    return max(1, (os.cpu_count() or 4) // 2)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "相片瘦身 WebApp"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DEBUG: bool = False
    
    # Base directories
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    THUMBS_CACHE_DIR: Path = BASE_DIR / "thumbs_cache"
    
    # Database
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/app.db"
    SYNC_DATABASE_URL: str = f"sqlite:///{BASE_DIR}/data/app.db"
    
    # Default Rule Thresholds
    BLUR_THRESHOLD: float = 100.0
    UNDER_EXPOSURE_THRESHOLD: float = 30.0
    OVER_EXPOSURE_THRESHOLD: float = 225.0
    PHASH_DISTANCE_THRESHOLD: int = 6
    BURST_TIME_WINDOW_SECONDS: float = 2.0

    # ── 分析階段並行 Runner 數目 ────────────────────────────────────────
    # 每個階段可獨立設定，亦可透過資料庫 Settings 表在前端覆寫。
    # 預設值：CPU 核心數 // 2（最少 1）。
    # clip 階段若使用 GPU，建議手動調為 1（GPU 推理單流水線最高效）。
    HASH_RUNNERS: int = _default_runners()
    BASIC_RUNNERS: int = _default_runners()
    CLIP_RUNNERS: int = _default_runners()
    VIDEO_RUNNERS: int = _default_runners()

settings = Settings()

# Ensure required directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.THUMBS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
