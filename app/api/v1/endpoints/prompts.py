from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTL_PROMPTS, cache_get, cache_set, key_prompts
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.db.session import get_session
from app.models.prompt import Prompt
from app.schemas.prompt import PromptResponse

logger = get_logger("prompts")

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("", response_model=list[PromptResponse])
async def get_prompts(
    response: Response,
    language: str = Query(
        "fa", min_length=2, max_length=5, description="Language code, e.g. 'fa' or 'en'"
    ),
    session: AsyncSession = Depends(get_session),
) -> list[PromptResponse]:
    """
    Get the list of active prompt questions in the requested language.

    Public, no auth required. Defaults to 'fa'. Only `is_active=True`
    prompts are returned — inactive prompts are kept in the DB (e.g. for
    existing UserPrompt answers referencing them) but no longer offered
    as a choice for new answers.
    """
    response.headers["Cache-Control"] = "public, max-age=86400"
    cached = await cache_get(redis_client, key_prompts(language))
    if cached:
        return [PromptResponse(**p) for p in cached]
    result = await session.execute(
        select(Prompt)
        .where(Prompt.language == language, Prompt.is_active == True)  # noqa: E712
        .order_by(Prompt.category, Prompt.id)
    )
    prompts = result.scalars().all()
    data = [PromptResponse.model_validate(p).model_dump(mode="json") for p in prompts]
    await cache_set(redis_client, key_prompts(language), data, TTL_PROMPTS)
    return [PromptResponse(**p) for p in data]
