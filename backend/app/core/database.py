from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings
from app.models.schema import Base

# Async Engine for FastAPI async routes
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False},
)

# Sync Engine for background workers / sync operations
sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False},
)

# Enable WAL Mode and foreign keys for SQLite
@event.listens_for(sync_engine, "connect")
def set_sqlite_pragma_sync(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.close()

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

def init_db():
    """Create all tables in SQLite database and configure WAL."""
    Base.metadata.create_all(bind=sync_engine)
