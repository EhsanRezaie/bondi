from sqlalchemy.exc import IntegrityError
import random
import string
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from sqlalchemy.orm import selectinload
from app.db.session import get_session
from app.core.deps import get_current_user, get_current_user_db
from app.core.limiter import limiter
from app.core.config import settings
from app.models.user import User
from app.models.referral_reward import ReferralReward
from app.services.reward_service import RewardService
from app.schemas.referral import ReferralCodeResponse, ClaimReferralResponse, ReferralStatsResponse
from app.core.redis import redis_client
from app.core.cache import invalidate_auth_user

from app.core.logging import get_logger

logger = get_logger("referrals")

router = APIRouter(prefix="/referrals", tags=["referrals"])


def generate_referral_code() -> str:
    """Generate unique 8-character referral code."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


@router.get("/my-code", response_model=ReferralCodeResponse)
@limiter.limit("30/minute")
async def get_my_referral_code(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_db),
):
    """Get user's referral code to share with friends."""
    # Generate code if missing (backward compatibility for existing users)
    if not current_user.referral_code:
        current_user.referral_code = generate_referral_code()
        await session.commit()
        await invalidate_auth_user(redis_client, current_user.id)
    
    return {
        "referral_code": current_user.referral_code,
        "share_text": f"Join me on DatingApp! Use my code: {current_user.referral_code} to get {settings.REFERRAL_INVITED_DAYS} free premium days!"
    }


@router.post("/claim", response_model=ClaimReferralResponse)
@limiter.limit("10/minute")
async def claim_referral(
    request: Request,
    body: dict,  # {"referral_code": "ABC12345"}
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_db),
):
    """
    Claim a referral code after registration.
    
    Can only be used once per user.
    Both inviter and invited get premium days.
    """
    referral_code = body.get("referral_code")
    if not referral_code:
        raise HTTPException(status_code=400, detail="Referral code required")
    
    # Check if user already claimed a referral
    existing_reward = await session.execute(
        select(ReferralReward).where(ReferralReward.invited_id == current_user.id)
    )
    if existing_reward.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Referral already claimed")
    
    # Find inviter by referral code
    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.referral_code == referral_code)
    )
    inviter = result.scalar_one_or_none()
    
    if not inviter:
        raise HTTPException(status_code=404, detail="Invalid referral code")
    
    if inviter.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot use your own referral code")
    
    # Create reward record
    reward = ReferralReward(
        inviter_id=inviter.id,
        invited_id=current_user.id,
        inviter_days=settings.REFERRAL_INVITER_DAYS,
        invited_days=settings.REFERRAL_INVITED_DAYS,
    )
    session.add(reward)
    
    # Grant rewards
    reward_service = RewardService(session)
    
    # Grant to invited user
    await reward_service.grant_premium_days(
        current_user,
        settings.REFERRAL_INVITED_DAYS,
        source="referral"
    )
    
    # Grant to inviter
    await reward_service.grant_premium_days(
        inviter,
        settings.REFERRAL_INVITER_DAYS,
        source="referral"
    )
    
    try:
        await session.commit()
        await invalidate_auth_user(redis_client, current_user.id)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Referral already claimed",
        )
    
    return {
        "success": True,
        "message": f"You received {settings.REFERRAL_INVITED_DAYS} free premium days! Your inviter also got {settings.REFERRAL_INVITER_DAYS} days.",
        "your_referral_code": current_user.referral_code
    }


@router.get("/stats", response_model=ReferralStatsResponse)
@limiter.limit("30/minute")
async def get_referral_stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_db),
):
    """Get referral statistics for current user."""
    # Count successful referrals
    result = await session.execute(
        select(func.count(ReferralReward.id)).where(ReferralReward.inviter_id == current_user.id)
    )
    referral_count = result.scalar() or 0
    
    # Calculate total reward days earned
    result = await session.execute(
        select(func.sum(ReferralReward.inviter_days)).where(ReferralReward.inviter_id == current_user.id)
    )
    total_days_earned = result.scalar() or 0
    
    # Get premium status
    premium_until = current_user.profile.premium_until.isoformat() if current_user.profile.premium_until else None
    
    # Generate code if missing
    if not current_user.referral_code:
        current_user.referral_code = generate_referral_code()
        await session.commit()
        await invalidate_auth_user(redis_client, current_user.id)
    
    return {
        "referral_code": current_user.referral_code,
        "successful_referrals": referral_count,
        "total_premium_days_earned": total_days_earned,
        "inviter_reward_days": settings.REFERRAL_INVITER_DAYS,
        "invited_reward_days": settings.REFERRAL_INVITED_DAYS,
        "is_premium": current_user.profile.is_premium,
        "premium_until": premium_until
    }