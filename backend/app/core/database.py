from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings
from app.models.schema import Base

_BUSY_TIMEOUT_S = settings.SQLITE_BUSY_TIMEOUT_MS / 1000.0

# Async Engine for FastAPI async routes
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False, "timeout": _BUSY_TIMEOUT_S},
)

# Sync Engine for background workers / sync operations
# pool 加大：hash/basic/video 階段最多 16 個 worker 各自持有一條連線
sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False, "timeout": _BUSY_TIMEOUT_S},
    pool_size=20,
    max_overflow=20,
)


def _apply_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute(f"PRAGMA busy_timeout={int(settings.SQLITE_BUSY_TIMEOUT_MS)};")
    cursor.close()


# 兩個 engine 都要套用 WAL / 外鍵 / busy_timeout（原本只套在 sync engine）
event.listen(sync_engine, "connect", _apply_sqlite_pragmas)
event.listen(async_engine.sync_engine, "connect", _apply_sqlite_pragmas)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


from app.core.migration import run_migrations


def init_db():
    """Create all tables in SQLite database, configure WAL, and run migrations."""
    from app.models.schema import Base as SchemaBase
    SchemaBase.metadata.create_all(bind=sync_engine)
    # 執行資料庫版本遷移（冪等）；備份路徑由實際資料庫 URL 推算
    run_migrations(sync_engine, settings.sqlite_db_path)
