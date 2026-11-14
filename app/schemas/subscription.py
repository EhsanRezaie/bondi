from datetime import datetime

from pydantic import BaseModel


class PlanResponse(BaseModel):
    id: str
    name: str
    days: int
    price_rials: int
    price_usd: float
    discount_percent: int


class SubscriptionPlansResponse(BaseModel):
    plans: list[PlanResponse]


class PurchaseRequest(BaseModel):
    plan_id: str


class PurchaseResponse(BaseModel):
    redirect_url: str
    authority: str


class VerifyResponse(BaseModel):
    success: bool
    message: str
    ref_id: str | None = None


class SubscriptionStatusResponse(BaseModel):
    is_premium: bool
    plan: str | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None
    source: str | None = None
    status: str | None = None


class CancelSubscriptionResponse(BaseModel):
    success: bool
    message: str
