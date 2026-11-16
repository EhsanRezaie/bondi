from pydantic import BaseModel


class AdRewardResponse(BaseModel):
    success: bool
    likes_added: int
    chats_added: int
    message: str
    ads_watched_today: int
    max_ads_per_day: int


class DailyLimitsResponse(BaseModel):
    is_premium: bool
    premium_expires_at: str | None = None
    likes_used_today: int
    likes_remaining_today: int
    chats_used_today: int
    chats_remaining_today: int
    ads_watched_today: int
    max_ads_per_day: int
    daily_likes_limit: int
    daily_chats_limit: int
    ad_reward_likes_bonus: int
    ad_reward_chats_bonus: int
