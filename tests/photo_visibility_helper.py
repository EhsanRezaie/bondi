"""Test helper: grant an approved photo to a user.

The discover/search visibility policy requires ALL of a user's photos to be
approved, so test users must have at least one approved photo to show up.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.photo import Photo
from tests.conftest import make_engine


async def grant_approved_photo(user_id: str) -> None:
    """Insert a single approved photo for the user (no S3 call needed)."""
    engine = make_engine()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                Photo(
                    user_id=user_id,
                    url=f"users/{user_id}/test.jpg",
                    order=0,
                    is_main=True,
                    status="approved",
                    face_verified=True,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
