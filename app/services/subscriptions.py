import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.subscription import Subscription
from app.models.user import User
from app.schemas.subscription import (
    CancelSubscriptionResponse,
    PlanResponse,
    PurchaseRequest,
    PurchaseResponse,
    SubscriptionPlansResponse,
    SubscriptionStatusResponse,
    VerifyResponse,
)

logger = get_logger("services.subscriptions")

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("/plans", response_model=SubscriptionPlansResponse)
@limiter.limit("100/minute")
async def get_plans():
    """Get available subscription plans with prices."""
    # Prices in Iranian Rials (Toman * 10)
    return SubscriptionPlansResponse(
        plans=[
            PlanResponse(
                id="monthly",
                name="Monthly",
                days=settings.SUBSCRIPTION_MONTHLY_DAYS,
                price_rials=50000,  # 5,000 Toman = 50,000 Rials
                price_usd=1.99,
                discount_percent=0,
            ),
            PlanResponse(
                id="quarterly",
                name="Quarterly",
                days=settings.SUBSCRIPTION_QUARTERLY_DAYS,
                price_rials=127500,  # 15% discount
                price_usd=5.07,
                discount_percent=settings.SUBSCRIPTION_QUARTERLY_DISCOUNT,
            ),
            PlanResponse(
                id="yearly",
                name="Yearly",
                days=settings.SUBSCRIPTION_YEARLY_DAYS,
                price_rials=420000,  # 30% discount
                price_usd=16.71,
                discount_percent=settings.SUBSCRIPTION_YEARLY_DISCOUNT,
            ),
        ]
    )


@router.post("/purchase", response_model=PurchaseResponse)
@limiter.limit("10/minute")
async def purchase_subscription(
    request: Request,
    body: PurchaseRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Purchase a subscription (MOCK - returns fake redirect URL).
    In production, this would integrate with ZarinPal.
    """
    # Map plan to days
    plan_days = {
        "monthly": settings.SUBSCRIPTION_MONTHLY_DAYS,
        "quarterly": settings.SUBSCRIPTION_QUARTERLY_DAYS,
        "yearly": settings.SUBSCRIPTION_YEARLY_DAYS,
    }

    if body.plan_id not in plan_days:
        raise HTTPException(status_code=400, detail="Invalid plan")

    # Mock: Generate fake authority
    mock_authority = str(uuid.uuid4()).replace("-", "")[:36]

    # In production: Call ZarinPal API here
    # For MVP: Return mock redirect URL
    mock_redirect_url = f"https://sandbox.zarinpal.com/pg/StartPay/{mock_authority}"

    # Store pending purchase in Redis (optional)
    # await redis.setex(f"purchase:{mock_authority}", 3600, str(current_user.id))

    return PurchaseResponse(redirect_url=mock_redirect_url, authority=mock_authority)


@router.get("/verify", response_model=VerifyResponse)
@limiter.limit("20/minute")
async def verify_payment(
    request: Request,
    authority: str,
    status: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Verify payment after ZarinPal redirect (MOCK).
    In production: Verify with ZarinPal API and activate subscription.
    """
    if status != "OK":
        return VerifyResponse(success=False, message="Payment was canceled or failed.", ref_id=None)

    # Mock: Find which user made this purchase
    # In production: Get user_id from Redis or database

    # For mock, we need user_id from query param or session
    user_id = request.query_params.get("user_id")
    plan = request.query_params.get("plan", "monthly")

    if not user_id:
        return VerifyResponse(success=False, message="User ID not found in callback.", ref_id=None)

    # Find user
    from sqlalchemy import select

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        return VerifyResponse(success=False, message="User not found.", ref_id=None)

    # Map plan to days
    plan_days = {
        "monthly": settings.SUBSCRIPTION_MONTHLY_DAYS,
        "quarterly": settings.SUBSCRIPTION_QUARTERLY_DAYS,
        "yearly": settings.SUBSCRIPTION_YEARLY_DAYS,
    }
    days = plan_days.get(plan, 30)

    # Grant premium
    now = datetime.now(UTC)
    if user.premium_until is None or user.premium_until < now:
        user.premium_until = now + timedelta(days=days)
    else:
        user.premium_until = user.premium_until + timedelta(days=days)

    # Create subscription record
    subscription = Subscription(
        user_id=user.id,
        plan=plan,
        status="active",
        started_at=now,
        expires_at=user.premium_until,
        source="purchase",
        payment_id=mock_ref_id,  # In production: get from ZarinPal
    )
    session.add(subscription)
    await session.commit()

    return VerifyResponse(
        success=True,
        message="Payment verified! Premium activated.",
        ref_id="MOCK_REF_123456",  # Mock reference
    )


@router.get("/my", response_model=SubscriptionStatusResponse)
@limiter.limit("30/minute")
async def get_my_subscription(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get current user's subscription status."""
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == current_user.id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    subscription = result.scalar_one_or_none()

    if not subscription or not current_user.profile.is_premium:
        return SubscriptionStatusResponse(
            is_premium=False, plan=None, started_at=None, expires_at=None, source=None, status=None
        )

    return SubscriptionStatusResponse(
        is_premium=True,
        plan=subscription.plan,
        started_at=subscription.started_at,
        expires_at=subscription.expires_at,
        source=subscription.source,
        status=subscription.status,
    )


@router.post("/cancel", response_model=CancelSubscriptionResponse)
@limiter.limit("10/minute")
async def cancel_subscription(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Cancel auto-renewal (does not remove existing premium)."""
    # Find active subscription
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == current_user.id, Subscription.status == "active")
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(status_code=404, detail="No active subscription found")

    # Mark as cancelled (premium remains until expiry)
    subscription.status = "cancelled"
    await session.commit()

    return CancelSubscriptionResponse(
        success=True, message="Auto-renewal cancelled. Your premium remains until expiry."
    )
