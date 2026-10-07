from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import uuid

from app.db.session import get_session
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.config import settings
from app.core.redis import redis_client
from app.core.cache import cache_get, cache_set, key_sub_plans, TTL_SUB_PLANS, invalidate_auth_user
from sqlalchemy.orm import selectinload
from app.models.user import User
from app.models.subscription import Subscription, PendingPurchase
from app.schemas.subscription import (
    SubscriptionPlansResponse,
    PlanResponse,
    PurchaseRequest,
    PurchaseResponse,
    VerifyResponse,
    SubscriptionStatusResponse,
    CancelSubscriptionResponse
)

from app.core.logging import get_logger

logger = get_logger("subscriptions")

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("/plans", response_model=SubscriptionPlansResponse)
@limiter.limit("100/minute")
async def get_plans(request: Request, response: Response):
    """Get available subscription plans with prices."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    cached = await cache_get(redis_client, key_sub_plans())
    if cached:
        return SubscriptionPlansResponse(**cached)
    plans = SubscriptionPlansResponse(
        plans=[
            PlanResponse(
                id="monthly",
                name="Monthly",
                days=settings.SUBSCRIPTION_MONTHLY_DAYS,
                price_rials=50000,
                price_usd=1.99,
                discount_percent=0,
            ),
            PlanResponse(
                id="quarterly",
                name="Quarterly",
                days=settings.SUBSCRIPTION_QUARTERLY_DAYS,
                price_rials=127500,
                price_usd=5.07,
                discount_percent=settings.SUBSCRIPTION_QUARTERLY_DISCOUNT,
            ),
            PlanResponse(
                id="yearly",
                name="Yearly",
                days=settings.SUBSCRIPTION_YEARLY_DAYS,
                price_rials=420000,
                price_usd=16.71,
                discount_percent=settings.SUBSCRIPTION_YEARLY_DISCOUNT,
            ),
        ]
    )
    await cache_set(redis_client, key_sub_plans(), plans.model_dump(mode='json'), TTL_SUB_PLANS)
    return plans


@router.post("/purchase", response_model=PurchaseResponse)
@limiter.limit("10/minute")
async def purchase_subscription(
    request: Request,  # Already has request
    body: PurchaseRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Initiate a subscription purchase (MOCK gateway).

    Records a PendingPurchase bound to the authenticated user so the callback
    can be verified server-side and consumed exactly once.
    """
    plan_days = {
        "monthly": settings.SUBSCRIPTION_MONTHLY_DAYS,
        "quarterly": settings.SUBSCRIPTION_QUARTERLY_DAYS,
        "yearly": settings.SUBSCRIPTION_YEARLY_DAYS,
    }
    plan_prices = {
        "monthly": 50000,
        "quarterly": 127500,
        "yearly": 420000,
    }

    if body.plan_id not in plan_days:
        raise HTTPException(status_code=400, detail="Invalid plan")

    authority = str(uuid.uuid4())
    purchase = PendingPurchase(
        authority=authority,
        user_id=current_user.id,
        plan=body.plan_id,
        amount_rials=plan_prices[body.plan_id],
        status="pending",
    )
    session.add(purchase)
    await session.commit()

    mock_redirect_url = f"https://sandbox.zarinpal.com/pg/StartPay/{authority}"

    return PurchaseResponse(
        redirect_url=mock_redirect_url,
        authority=authority
    )


@router.get("/verify", response_model=VerifyResponse)
@limiter.limit("20/minute")
async def verify_payment(
    request: Request,  # Already has request
    authority: str,
    status: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Verify a gateway callback and activate premium exactly once.

    The buyer is derived from the access token, never from the query string,
    and each ``authority`` can be consumed only once.
    """
    if status != "OK":
        return VerifyResponse(
            success=False,
            message="Payment was canceled or failed.",
            ref_id=None
        )

    result = await session.execute(
        select(PendingPurchase)
        .where(PendingPurchase.authority == authority)
        .with_for_update()
    )
    purchase = result.scalar_one_or_none()

    if not purchase:
        return VerifyResponse(
            success=False,
            message="Unknown payment reference.",
            ref_id=None
        )

    if purchase.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Payment does not belong to this user",
        )

    if purchase.status != "pending":
        return VerifyResponse(
            success=False,
            message="Payment already processed.",
            ref_id=None
        )

    plan_days = {
        "monthly": settings.SUBSCRIPTION_MONTHLY_DAYS,
        "quarterly": settings.SUBSCRIPTION_QUARTERLY_DAYS,
        "yearly": settings.SUBSCRIPTION_YEARLY_DAYS,
    }
    days = plan_days.get(purchase.plan, 0)
    if days <= 0:
        return VerifyResponse(
            success=False,
            message="Invalid plan on payment.",
            ref_id=None
        )

    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == current_user.id).with_for_update()
    )
    user = result.scalar_one_or_none()

    if not user or not user.profile:
        return VerifyResponse(
            success=False,
            message="User not found.",
            ref_id=None
        )

    now = datetime.now(timezone.utc)
    if user.profile.premium_until is None or user.profile.premium_until < now:
        user.profile.premium_until = now + timedelta(days=days)
    else:
        user.profile.premium_until = user.profile.premium_until + timedelta(days=days)

    purchase.status = "consumed"
    purchase.consumed_at = now

    subscription = Subscription(
        user_id=user.id,
        plan=purchase.plan,
        status="active",
        started_at=now,
        expires_at=user.profile.premium_until,
        source="purchase",
        payment_id="MOCK_" + authority,
    )
    session.add(subscription)
    await session.commit()

    await invalidate_auth_user(redis_client, user.id)

    return VerifyResponse(
        success=True,
        message="Payment verified! Premium activated.",
        ref_id="MOCK_REF_" + authority[:8]
    )


@router.get("/my", response_model=SubscriptionStatusResponse)
@limiter.limit("30/minute")
async def get_my_subscription(
    request: Request,  # ADDED request parameter
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
            is_premium=False,
            plan=None,
            started_at=None,
            expires_at=None,
            source=None,
            status=None
        )
    
    return SubscriptionStatusResponse(
        is_premium=True,
        plan=subscription.plan,
        started_at=subscription.started_at,
        expires_at=subscription.expires_at,
        source=subscription.source,
        status=subscription.status
    )


@router.post("/cancel", response_model=CancelSubscriptionResponse)
@limiter.limit("10/minute")
async def cancel_subscription(
    request: Request,  # ADDED request parameter
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Cancel auto-renewal (does not remove existing premium)."""
    result = await session.execute(
        select(Subscription)
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status == "active"
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        raise HTTPException(status_code=404, detail="No active subscription found")
    
    subscription.status = "cancelled"
    await session.commit()
    
    return CancelSubscriptionResponse(
        success=True,
        message="Auto-renewal cancelled. Your premium remains until expiry."
    )