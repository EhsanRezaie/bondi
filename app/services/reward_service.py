# app/services/reward_service.py
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, literal
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.models.user import User
from app.models.daily_limit import DailyLimit
from app.models.subscription import Subscription
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.core.cache import cache_get, cache_set, key_daily_limits

logger = get_logger("reward_service")


def _seconds_until_midnight() -> int:
    now = datetime.now()
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((midnight - now).total_seconds())


class RewardService:
    """Handles daily limits, ad rewards, and premium bonuses."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_or_create_daily_limit(self, user_id: UUID, target_date: date) -> DailyLimit:
        """Get or create daily limit record for a user."""
        # Try Redis cache first
        cache_key = key_daily_limits(user_id, target_date.isoformat())
        cached = await cache_get(redis_client, cache_key)
        if cached:
            return DailyLimit(
                user_id=user_id,
                date=target_date,
                likes_used=cached.get("likes_used", 0),
                chats_used=cached.get("chats_used", 0),
                ad_likes_bonus=cached.get("ad_likes_bonus", 0),
                ad_chats_bonus=cached.get("ad_chats_bonus", 0),
            )

        stmt = insert(DailyLimit).values(
            user_id=user_id,
            date=target_date,
            likes_used=0,
            chats_used=0,
            ad_likes_bonus=0,
            ad_chats_bonus=0,
        ).on_conflict_do_nothing(
            constraint="uq_daily_limits_user_date"
        ).returning(DailyLimit)

        result = await self.db.execute(stmt)
        daily_limit = result.scalar_one_or_none()

        if not daily_limit:
            # Row already existed (conflict) — fetch existing
            result = await self.db.execute(
                select(DailyLimit).where(
                    DailyLimit.user_id == user_id,
                    DailyLimit.date == target_date,
                )
            )
            daily_limit = result.scalar_one()

        await self.db.flush()

        # Cache for the rest of the day
        await cache_set(redis_client, cache_key, {
            "likes_used": daily_limit.likes_used,
            "chats_used": daily_limit.chats_used,
            "ad_likes_bonus": daily_limit.ad_likes_bonus,
            "ad_chats_bonus": daily_limit.ad_chats_bonus,
        }, _seconds_until_midnight())
        
        return daily_limit
    
    async def get_remaining_likes(self, user_id: UUID, is_premium: bool) -> int:
        """Get remaining likes for today. Returns -1 for unlimited."""
        if is_premium:
            return -1
        
        today = date.today()
        daily_limit = await self.get_or_create_daily_limit(user_id, today)
        
        available = settings.FREE_USER_DAILY_LIKES + daily_limit.ad_likes_bonus - daily_limit.likes_used
        return max(0, available)
    
    async def get_remaining_chats(self, user: User) -> int:
        """Get remaining new chats for today. Returns -1 for unlimited."""
        # ✅ FIX: Check profile exists and is_premium
        if user.profile and user.profile.is_premium:
            return -1
        
        today = date.today()
        daily_limit = await self.get_or_create_daily_limit(user.id, today)
        
        available = settings.FREE_USER_DAILY_CHATS + daily_limit.ad_chats_bonus - daily_limit.chats_used
        return max(0, available)
    
    async def consume_like(self, user_id: UUID, is_premium: bool) -> bool:
        """Consume one like atomically. Returns True if successful."""
        if is_premium:
            return True

        today = date.today()

        # Atomic: increment only if under the limit (prevents TOCTOU race)
        stmt = (
            update(DailyLimit)
            .where(
                DailyLimit.user_id == user_id,
                DailyLimit.date == today,
                DailyLimit.likes_used < literal(settings.FREE_USER_DAILY_LIKES) + DailyLimit.ad_likes_bonus,
            )
            .values(likes_used=DailyLimit.likes_used + 1)
            .returning(DailyLimit)
        )
        result = await self.db.execute(stmt)
        updated = result.scalar_one_or_none()

        if not updated:
            return False

        await self.db.commit()

        # Update Redis cache
        cache_key = key_daily_limits(user_id, today.isoformat())
        await cache_set(redis_client, cache_key, {
            "likes_used": updated.likes_used,
            "chats_used": updated.chats_used,
            "ad_likes_bonus": updated.ad_likes_bonus,
            "ad_chats_bonus": updated.ad_chats_bonus,
        }, _seconds_until_midnight())

        return True
    
    async def consume_chat(self, user: User) -> bool:
        """Consume one new chat atomically. Returns True if successful."""
        if user.profile and user.profile.is_premium:
            return True

        today = date.today()

        # Atomic: increment only if under the limit (prevents TOCTOU race)
        stmt = (
            update(DailyLimit)
            .where(
                DailyLimit.user_id == user.id,
                DailyLimit.date == today,
                DailyLimit.chats_used < literal(settings.FREE_USER_DAILY_CHATS) + DailyLimit.ad_chats_bonus,
            )
            .values(chats_used=DailyLimit.chats_used + 1)
            .returning(DailyLimit)
        )
        result = await self.db.execute(stmt)
        updated = result.scalar_one_or_none()

        if not updated:
            return False

        await self.db.commit()

        # Update Redis cache
        cache_key = key_daily_limits(user.id, today.isoformat())
        await cache_set(redis_client, cache_key, {
            "likes_used": updated.likes_used,
            "chats_used": updated.chats_used,
            "ad_likes_bonus": updated.ad_likes_bonus,
            "ad_chats_bonus": updated.ad_chats_bonus,
        }, _seconds_until_midnight())

        return True
    
    async def claim_ad_reward(self, user: User) -> dict:
        """
        Claim reward for watching an ad atomically.
        Returns: {'success': bool, 'likes_added': int, 'chats_added': int, 'message': str}
        """
        today = date.today()

        # Ensure daily limit row exists before atomic update
        await self.get_or_create_daily_limit(user.id, today)

        # Atomic: increment bonuses only if under the ad limit (prevents TOCTOU race)
        stmt = (
            update(DailyLimit)
            .where(
                DailyLimit.user_id == user.id,
                DailyLimit.date == today,
                (DailyLimit.ad_likes_bonus / literal(settings.AD_REWARD_LIKES_BONUS)) < literal(settings.MAX_AD_REWARDS_PER_DAY),
            )
            .values(
                ad_likes_bonus=DailyLimit.ad_likes_bonus + settings.AD_REWARD_LIKES_BONUS,
                ad_chats_bonus=DailyLimit.ad_chats_bonus + settings.AD_REWARD_CHATS_BONUS,
            )
            .returning(DailyLimit)
        )
        result = await self.db.execute(stmt)
        updated = result.scalar_one_or_none()

        if not updated:
            # Read current state for the error response
            daily_limit = await self.get_or_create_daily_limit(user.id, today)
            current_ad_count = daily_limit.ad_likes_bonus // settings.AD_REWARD_LIKES_BONUS
            return {
                'success': False,
                'likes_added': 0,
                'chats_added': 0,
                'message': f'Daily ad limit reached ({settings.MAX_AD_REWARDS_PER_DAY} ads per day)',
                'ads_watched_today': current_ad_count,
                'max_ads_per_day': settings.MAX_AD_REWARDS_PER_DAY
            }

        await self.db.commit()

        # Update Redis cache
        cache_key = key_daily_limits(user.id, today.isoformat())
        await cache_set(redis_client, cache_key, {
            "likes_used": updated.likes_used,
            "chats_used": updated.chats_used,
            "ad_likes_bonus": updated.ad_likes_bonus,
            "ad_chats_bonus": updated.ad_chats_bonus,
        }, _seconds_until_midnight())

        new_ad_count = updated.ad_likes_bonus // settings.AD_REWARD_LIKES_BONUS
        return {
            'success': True,
            'likes_added': settings.AD_REWARD_LIKES_BONUS,
            'chats_added': settings.AD_REWARD_CHATS_BONUS,
            'message': f'You gained +{settings.AD_REWARD_LIKES_BONUS} likes and +{settings.AD_REWARD_CHATS_BONUS} new chats for today!',
            'ads_watched_today': new_ad_count,
            'max_ads_per_day': settings.MAX_AD_REWARDS_PER_DAY
        }
    
    async def get_daily_stats(self, user: User) -> dict:
        """Get complete daily stats for user."""
        today = date.today()
        daily_limit = await self.get_or_create_daily_limit(user.id, today)
        
        # ✅ FIX: Check profile exists and is_premium
        if user.profile and user.profile.is_premium:
            return {
                'is_premium': True,
                'premium_expires_at': user.profile.premium_until.isoformat() if user.profile.premium_until else None,
                'likes_used_today': daily_limit.likes_used,
                'likes_remaining_today': -1,
                'chats_used_today': daily_limit.chats_used,
                'chats_remaining_today': -1,
                'ads_watched_today': daily_limit.ad_likes_bonus // settings.AD_REWARD_LIKES_BONUS,
                'max_ads_per_day': settings.MAX_AD_REWARDS_PER_DAY,
                'daily_likes_limit': -1,
                'daily_chats_limit': -1,
                'ad_reward_likes_bonus': settings.AD_REWARD_LIKES_BONUS,
                'ad_reward_chats_bonus': settings.AD_REWARD_CHATS_BONUS,
            }
        
        likes_limit = settings.FREE_USER_DAILY_LIKES + daily_limit.ad_likes_bonus
        chats_limit = settings.FREE_USER_DAILY_CHATS + daily_limit.ad_chats_bonus
        
        return {
            'is_premium': False,
            'premium_expires_at': None,
            'likes_used_today': daily_limit.likes_used,
            'likes_remaining_today': max(0, likes_limit - daily_limit.likes_used),
            'chats_used_today': daily_limit.chats_used,
            'chats_remaining_today': max(0, chats_limit - daily_limit.chats_used),
            'ads_watched_today': daily_limit.ad_likes_bonus // settings.AD_REWARD_LIKES_BONUS,
            'max_ads_per_day': settings.MAX_AD_REWARDS_PER_DAY,
            'daily_likes_limit': likes_limit,
            'daily_chats_limit': chats_limit,
            'ad_reward_likes_bonus': settings.AD_REWARD_LIKES_BONUS,
            'ad_reward_chats_bonus': settings.AD_REWARD_CHATS_BONUS,
        }
    
    async def grant_premium_days(self, user: User, days: int, source: str, payment_id: str = None) -> datetime:
        """Grant premium days to user and create subscription record."""
        now = datetime.now(timezone.utc)
        
        # ✅ FIX: Use user.profile.premium_until
        profile = user.profile
        if not profile:
            logger.error("User has no profile", user_id=str(user.id))
            raise ValueError("User profile not found")
        
        # Calculate new expiry
        if profile.premium_until is None or profile.premium_until < now:
            profile.premium_until = now + timedelta(days=days)
        else:
            profile.premium_until = profile.premium_until + timedelta(days=days)
        
        # Create subscription record
        subscription = Subscription(
            user_id=user.id,
            plan=f"{days}_days",
            status="active",
            started_at=now,
            expires_at=profile.premium_until,
            source=source,
            payment_id=payment_id,
        )
        self.db.add(subscription)
        await self.db.commit()
        
        return profile.premium_until