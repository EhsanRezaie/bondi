from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("db.session")

# Connection budget (Phase 2.2):
# entrypoint caps web workers at 8 → worst case 8 x (10+10) = 160 connections,
# leaving ~40 headroom of max_connections=200 for Celery (separate container,
# same engine config per prefork process), admin scripts and migrations.
_engine_pool_size = 10
_engine_max_overflow = 10

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=_engine_pool_size,
    max_overflow=_engine_max_overflow,
    pool_recycle=1800,
    pool_timeout=30,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        # Server-side timeouts (ms) — a stuck query/lock/leaked session must
        # release its connection instead of starving the pool. Alembic builds
        # its own bare engine, so migrations are NOT affected by these.
        "server_settings": {
            "statement_timeout": "15000",  # no query runs > 15s
            "lock_timeout": "5000",  # no request hangs > 5s on a blocked lock
            "idle_in_transaction_session_timeout": "30000",  # kill leaked sessions
        },
    },
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except StarletteHTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("database_session_rollback")
            raise


# Alias for backwards compatibility
get_db = get_session
