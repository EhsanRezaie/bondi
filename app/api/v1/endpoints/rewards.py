from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.user import User
from app.schemas.rewards import AdRewardResponse, DailyLimitsResponse
from app.services.reward_service import RewardService

logger = get_logger("rewards")

router = APIRouter(prefix="/rewards", tags=["rewards"])


@router.post("/ad-watched", response_model=AdRewardResponse)
@limiter.limit("10/minute")
async def claim_ad_reward(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Claim reward for watching a rewarded ad.

    Each ad gives bonus likes and chats for the current day.
    Limited to MAX_AD_REWARDS_PER_DAY (from .env).
    """
    reward_service = RewardService(session)
    result = await reward_service.claim_ad_reward(current_user)

    if not result["success"]:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=result["message"])

    return result


@router.get("/my-limits", response_model=DailyLimitsResponse)
@limiter.limit("30/minute")
async def get_my_limits(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get current user's daily limits and usage.

    Returns:
    - For free users: shows remaining likes/chats for today
    - For premium users: shows unlimited (-1)
    """
    reward_service = RewardService(session)
    return await reward_service.get_daily_stats(current_user)
