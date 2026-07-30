from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("db.session")

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
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