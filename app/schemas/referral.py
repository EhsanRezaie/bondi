from pydantic import BaseModel


class ReferralCodeResponse(BaseModel):
    referral_code: str
    share_text: str


class ClaimReferralRequest(BaseModel):
    referral_code: str


class ClaimReferralResponse(BaseModel):
    success: bool
    message: str
    your_referral_code: str


class ReferralStatsResponse(BaseModel):
    referral_code: str
    successful_referrals: int
    total_premium_days_earned: int
    inviter_reward_days: int
    invited_reward_days: int
    is_premium: bool
    premium_until: str | None = None
