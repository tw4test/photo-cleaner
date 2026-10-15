import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_runners() -> int:
    """預設 runner 數：CPU 核心數的一半，最少 1。"""
    return min(16, max(1, (os.cpu_count() or 4) // 2))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "相片瘦身 WebApp"
    APP_VERSION: str = "0.5.0"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DEBUG: bool = False

    # Base directories
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    THUMBS_CACHE_DIR: Path = BASE_DIR / "thumbs_cache"

    # Database（要搬移資料庫時，兩個 URL 都要改）
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/app.db"
    SYNC_DATABASE_URL: str = f"sqlite:///{BASE_DIR}/data/app.db"
    SQLITE_BUSY_TIMEOUT_MS: int = 30000

    # ── 分析階段並行 Runner 數目 ────────────────────────────────────────
    # 每個階段可獨立設定，亦可透過資料庫 Settings 表在前端覆寫。
    # clip 階段的 runner 數 = 讀圖/前處理執行緒數；GPU 推理永遠只有一條流水線。
    HASH_RUNNERS: int = _default_runners()
    BASIC_RUNNERS: int = _default_runners()
    CLIP_RUNNERS: int = _default_runners()
    VIDEO_RUNNERS: int = _default_runners()

    # CLIP 批次大小：0 = 依顯存自動決定
    CLIP_BATCH_SIZE: int = 0

    @property
    def sqlite_db_path(self) -> Path:
        """由 SYNC_DATABASE_URL 推算實際資料庫檔案位置（供遷移前備份使用）。"""
        url = self.SYNC_DATABASE_URL
        prefix = "sqlite:///"
        if url.startswith(prefix):
            return Path(url[len(prefix):])
        return self.DATA_DIR / "app.db"


settings = Settings()

# Ensure required directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.THUMBS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
settings.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
