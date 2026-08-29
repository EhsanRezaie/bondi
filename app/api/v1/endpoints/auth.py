from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
import random
import string
from datetime import datetime, timedelta, timezone

from app.db.session import get_session
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_settings import UserSettings
from app.models.subscription import Subscription
from app.models.user_interest import UserInterest
from app.models.user_prompt import UserPrompt
from app.models.interest import Interest
import app.core.redis as redis
from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    decode_token,
)
from app.core.cache import invalidate_auth_user, invalidate_user_cache
from app.services.sms_service import send_verification_code

from app.schemas.auth import (
    RequestCodeRequest,
    RequestCodeResponse,
    VerifyCodeRequest,
    VerifyCodeResponse,
    OnboardingCompleteRequest,
    AuthResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    LogoutRequest,
)
from app.schemas.user import UserProfileResponse

from app.core.logging import get_logger

logger = get_logger("auth")

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_TTL = 300
OTP_RESEND_COOLDOWN = 60


def generate_referral_code() -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


def generate_verification_code() -> str:
    return ''.join(random.choices(string.digits, k=6))


async def get_user_profile(session: AsyncSession, user_id: str) -> UserProfile | None:
    result = await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_with_profile(session: AsyncSession, user_id: str) -> User | None:
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
        )
        .where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def build_login_response(
    user: User,
    session: AsyncSession,
    access_token: str = None,
    refresh_token: str = None,
) -> AuthResponse:
    if access_token is None:
        access_token = create_access_token(str(user.id), user.token_version)
    if refresh_token is None:
        refresh_token = create_refresh_token(str(user.id), user.token_version)
    
    await redis.store_refresh_token(refresh_token, str(user.id))
    
    # Always load full user (with relationships) fresh from the database so the
    # response matches GET /users/me — including profile_completion, interests,
    # prompts, settings, photos, etc. A hand-built partial response would leave
    # those fields at their defaults until the client refreshes /users/me.
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
            selectinload(User.user_interests).selectinload(UserInterest.interest),
            selectinload(User.prompts).selectinload(UserPrompt.prompt),
            selectinload(User.photos),
        )
        .where(User.id == user.id)
    )
    full_user = result.scalar_one_or_none() or user
    
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserProfileResponse.model_validate(full_user),
    )


async def get_user_by_phone(session: AsyncSession, phone: str) -> User | None:
    result = await session.execute(select(User).where(User.phone == phone))
    return result.scalar_one_or_none()


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token.")
    token = auth_header.split(" ", 1)[1]
    
    payload = decode_token(token, "access")
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    
    user_id = payload.get("sub")
    token_version = payload.get("ver", 1)
    
    user = await get_user_with_profile(session, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or deactivated.")
    
    if token_version != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token revoked. Please login again.",
        )
    
    return user


async def create_user_profile(user: User, session: AsyncSession) -> UserProfile:
    profile = UserProfile(user_id=user.id)
    session.add(profile)
    await session.flush()
    return profile


async def create_user_settings(user: User, session: AsyncSession) -> UserSettings:
    settings = UserSettings(user_id=user.id)
    session.add(settings)
    await session.flush()
    return settings


@router.post("/request-code", response_model=RequestCodeResponse)
@limiter.limit("5/minute")
async def request_code(
    request: Request,
    body: RequestCodeRequest,
    session: AsyncSession = Depends(get_session),
):
    phone = body.phone

    # Resend cooldown: prevent SMS spam
    if await redis.is_in_otp_cooldown(phone):
        remaining = await redis.get_otp_cooldown(phone)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining} seconds before requesting a new code.",
        )

    # Generate and store the code. Never reveal whether the number is
    # registered (anti-enumeration).
    code = generate_verification_code()
    await redis.store_verification_code(phone, code, ttl=OTP_TTL)
    await redis.store_otp_cooldown(phone, OTP_RESEND_COOLDOWN)
    await send_verification_code(phone, code)

    # Track registration IP for abuse detection (3+ from same IP in 24h = suspicious)
    client_ip = request.client.host if request.client else "unknown"
    ip_key = f"reg_ip:{client_ip}"
    try:
        pipe = redis.redis_client.pipeline()
        incr_result = pipe.incr(ip_key)
        pipe.expire(ip_key, 86400)
        results = await pipe.execute()
        ip_count = results[0]
        if ip_count >= 3:
            logger.warning("suspicious_registration_pattern", ip=client_ip, count=ip_count)
    except Exception as e:
        logger.warning("registration_ip_tracking_failed", error=str(e), exc_info=True)

    return RequestCodeResponse(
        message="If this phone number is registered, a verification code has been sent.",
        phone=phone,
        expires_in=OTP_TTL,
        resend_in=OTP_RESEND_COOLDOWN,
    )


@router.post("/verify-code", response_model=VerifyCodeResponse)
@limiter.limit("10/minute")
async def verify_code(
    request: Request,
    body: VerifyCodeRequest,
    session: AsyncSession = Depends(get_session),
):
    await redis.verify_code_with_attempts(body.phone, body.code)

    existing = await get_user_by_phone(session, body.phone)
    is_new_user = False

    if existing and not existing.is_active:
        # Deleted-account handling: restore within the grace window, otherwise
        # let the purge sweep clear it and keep the phone locked in the interim.
        if existing.deleted_at is not None:
            grace_end = existing.deleted_at + timedelta(days=settings.DELETE_ACCOUNT_GRACE_DAYS)
            if datetime.now(timezone.utc) < grace_end:
                # Within the grace window → restore the account (user changed their mind).
                existing.is_active = True
                existing.deleted_at = None
                existing.deleted_reason = None
                existing.token_version += 1
                existing.last_seen_at = datetime.now(timezone.utc)
                await session.flush()
                await invalidate_user_cache(redis.redis_client, existing.id)
                await invalidate_auth_user(redis.redis_client, existing.id)
            else:
                # Grace expired but the purge sweep hasn't run yet (≤24h window).
                # Keep the phone locked and point the user at retrying shortly.
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account deletion is being finalized. This number will become available shortly.",
                )
        else:
            # Deactivated for another reason (e.g. admin ban) — not restorable.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been deactivated.",
            )

    if existing:
        user = existing
        user.last_seen_at = datetime.now(timezone.utc)
    else:
        is_new_user = True
        user = User(
            phone=body.phone,
            phone_verified=True,
            registration_status="phone_verified",
            token_version=1,
            referral_code=generate_referral_code(),
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            logger.warning("verify_code_duplicate_phone", phone=body.phone, error=str(e), exc_info=True)
            # A concurrent request created the account; reload it and log in.
            existing_user = await get_user_by_phone(session, body.phone)
            if not existing_user:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with this phone number already exists.",
                )
            user = existing_user
            user.last_seen_at = datetime.now(timezone.utc)
            is_new_user = False
        
        if is_new_user:
            await create_user_profile(user, session)
            await create_user_settings(user, session)

            if body.referral_code:
                result = await session.execute(
                    select(User).where(User.referral_code == body.referral_code)
                )
                referred_by_user = result.scalar_one_or_none()
                if referred_by_user:
                    user.referred_by = referred_by_user.id
                    await session.flush()

    await session.commit()
    await redis.delete_verification_code(body.phone)

    access_token = create_access_token(str(user.id), user.token_version)
    refresh_token = create_refresh_token(str(user.id), user.token_version)
    await redis.store_refresh_token(refresh_token, str(user.id))

    # Load full user (with relationships) so the login response matches
    # GET /users/me — including profile_completion, interests, prompts,
    # settings, photos, etc.
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.settings),
            selectinload(User.user_interests).selectinload(UserInterest.interest),
            selectinload(User.prompts).selectinload(UserPrompt.prompt),
            selectinload(User.photos),
        )
        .where(User.id == user.id)
    )
    full_user = result.scalar_one_or_none() or user

    return VerifyCodeResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserProfileResponse.model_validate(full_user),
        is_new_user=is_new_user,
    )


@router.post("/register/complete", response_model=AuthResponse)
@limiter.limit("10/minute")
async def register_complete(
    request: Request,
    body: OnboardingCompleteRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.registration_status == "onboarding_complete":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile is already complete.",
        )
    
    if not current_user.profile:
        await create_user_profile(current_user, session)
        await session.flush()
    
    # Reload profile after creation to ensure it's attached
    profile = await get_user_profile(session, str(current_user.id))
    if not profile:
        # If still not found, create it again
        profile = UserProfile(user_id=current_user.id)
        session.add(profile)
        await session.flush()
    
    # Update profile fields
    profile.name = body.name
    profile.birth_date = body.birth_date
    profile.gender = body.gender
    profile.sexual_orientation = body.sexual_orientation
    profile.bio = body.bio
    profile.height = body.height
    profile.weight = body.weight
    profile.body_type = body.body_type
    profile.relationship_status = body.relationship_status
    profile.living_situation = body.living_situation
    profile.children_status = body.children_status
    profile.smoking = body.smoking
    profile.drinking = body.drinking
    profile.here_for = body.here_for
    profile.pets = body.pets
    profile.workout_frequency = body.workout_frequency
    profile.zodiac_sign = body.zodiac_sign
    profile.education = body.education
    profile.workplace = body.workplace
    profile.religion = body.religion
    profile.ethnicity = body.ethnicity
    profile.political_orientation = body.political_orientation
    profile.lat = body.lat
    profile.lng = body.lng
    profile.country = body.country
    profile.province = body.province
    profile.city = body.city
    profile.languages = body.languages
    
    if not profile.premium_until or profile.premium_until < datetime.now(timezone.utc):
        profile.premium_until = datetime.now(timezone.utc) + timedelta(days=settings.WELCOME_BONUS_DAYS)
    
    current_user.registration_status = "onboarding_complete"
    
    if body.interests:
        for interest_name in body.interests:
            interest_result = await session.execute(
                select(Interest).where(Interest.name == interest_name)
            )
            interest = interest_result.scalar_one_or_none()
            if interest:
                session.add(UserInterest(
                    user_id=current_user.id,
                    interest_id=interest.id
                ))
    
    if body.prompts:
        for prompt_data in body.prompts:
            session.add(UserPrompt(
                user_id=current_user.id,
                prompt_id=prompt_data.prompt_id,
                answer=prompt_data.answer
            ))
    
    subscription = Subscription(
        user_id=current_user.id,
        plan="welcome_bonus",
        status="active",
        started_at=datetime.now(timezone.utc),
        expires_at=profile.premium_until,
        source="welcome_bonus",
    )
    session.add(subscription)
    
    await session.commit()

    await invalidate_auth_user(redis.redis_client, current_user.id)

    return await build_login_response(current_user, session)


@router.post("/refresh", response_model=RefreshTokenResponse)
@limiter.limit("20/minute")
async def refresh(
    request: Request,
    body: RefreshTokenRequest,
    session: AsyncSession = Depends(get_session),
):
    user_id = decode_refresh_token(body.refresh_token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    stored_user_id = await redis.get_refresh_token_owner(body.refresh_token)
    if not stored_user_id or stored_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked.",
        )

    # Refresh-rotation theft detection: if this token was already rotated,
    # it's reuse → revoke the whole family so the attacker is locked out too.
    family_id = await redis.get_token_family(body.refresh_token)
    if await redis.is_rotated_token(body.refresh_token):
        if family_id:
            await redis.revoke_refresh_family(family_id)
        await redis.revoke_all_user_tokens(user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked.",
        )

    user = await get_user_with_profile(session, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated.",
        )

    family_id = await redis.get_token_family(body.refresh_token) or str(user.id)
    await redis.mark_token_rotated(body.refresh_token, family_id)

    new_access = create_access_token(str(user.id), user.token_version)
    new_refresh = create_refresh_token(str(user.id), user.token_version)
    await redis.store_refresh_token(new_refresh, str(user.id), family_id=family_id)

    return RefreshTokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer"
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def logout(request: Request, body: LogoutRequest):
    await redis.revoke_refresh_token(body.refresh_token)


@router.get("/health")
async def auth_health():
    redis_ok = await redis.health_check()
    return {
        "status": "healthy" if redis_ok else "degraded",
        "redis": "connected" if redis_ok else "disconnected",
    }
