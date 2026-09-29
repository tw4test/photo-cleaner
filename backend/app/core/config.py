from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

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

settings = Settings()

# Ensure required directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.THUMBS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
