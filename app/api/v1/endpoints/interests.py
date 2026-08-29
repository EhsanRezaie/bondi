from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_session
from app.models.interest import Interest
from app.schemas.interest import InterestResponse
from app.core.redis import redis_client
from app.core.cache import cache_get, cache_set, key_interests, TTL_INTERESTS

from app.core.logging import get_logger

logger = get_logger("interests")

router = APIRouter(prefix="/interests", tags=["interests"])

# Language codes we ship translations for in the seed data. Requests for any
# other code fall back to the stable `name`/`category` keys.
SUPPORTED_LANGUAGES = {"fa", "en"}


def _resolve_localized(interest: Interest, language: str) -> dict:
    """Build the per-interest response, resolving localized labels.

    `name`/`category` always stay the stable keys. `*_localized` carry the
    display text for the requested language, falling back to the raw key when
    the translation is missing (protects newly added or unlisted interests).
    """
    translations = interest.translations or {}
    entry = translations.get(language, {}) if isinstance(translations, dict) else {}

    name_localized = entry.get("name") if isinstance(entry, dict) else None
    category_localized = entry.get("category") if isinstance(entry, dict) else None

    return {
        "id": interest.id,
        "name": interest.name,
        "name_localized": name_localized or interest.name,
        "category": interest.category,
        "category_localized": category_localized or interest.category,
        "icon": interest.icon,
    }


@router.get("", response_model=list[InterestResponse])
async def get_interests(
    response: Response,
    language: str = Query("en", min_length=2, max_length=5, description="Language code, e.g. 'fa' or 'en'"),
    session: AsyncSession = Depends(get_session),
) -> list[InterestResponse]:
    """
    Get the full list of selectable interests in the requested language.

    Public, no auth required — static reference data used to populate the
    interests picker during onboarding and profile editing. `name`/`category`
    are stable keys; `name_localized`/`category_localized` are the display
    labels for `language` (fallback: the stable key). Results are cached per
    language for 24h.
    """
    language = language.lower()
    if language not in SUPPORTED_LANGUAGES:
        language = "en"

    response.headers["Cache-Control"] = "public, max-age=86400"
    cache_key = key_interests(language)
    cached = await cache_get(redis_client, cache_key)
    if cached:
        return [InterestResponse(**i) for i in cached]

    result = await session.execute(
        select(Interest).order_by(Interest.category, Interest.name)
    )
    interests = result.scalars().all()
    data = [_resolve_localized(i, language) for i in interests]
    await cache_set(redis_client, cache_key, data, TTL_INTERESTS)
    return [InterestResponse(**i) for i in data]
