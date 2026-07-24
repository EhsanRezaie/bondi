from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
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
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    decode_token,
)
from app.services.email_service import send_verification_code

from app.schemas.auth import (
    RegisterInitRequest,
    RegisterInitResponse,
    RegisterVerifyRequest,
    RegisterVerifyResponse,
    OnboardingCompleteRequest,
    LoginRequest,
    LoginResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    GoogleLoginRequest,
    LogoutRequest,
    PasswordResetRequest,
    PasswordResetVerifyRequest,
)
from app.schemas.user import UserProfileResponse

from app.core.logging import get_logger

logger = get_logger("auth")

router = APIRouter(prefix="/auth", tags=["auth"])


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
) -> LoginResponse:
    if access_token is None:
        access_token = create_access_token(str(user.id), user.token_version)
    if refresh_token is None:
        refresh_token = create_refresh_token(str(user.id), user.token_version)
    
    await redis.store_refresh_token(refresh_token, str(user.id))
    
    # Always load profile fresh from database
    profile = await get_user_profile(session, str(user.id))
    
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserProfileResponse(
            id=user.id,
            email=user.email,
            name=profile.name if profile else None,
            age=profile.age if profile else None,
            gender=profile.gender if profile else None,
            is_premium=profile.is_premium if profile else False,
            is_active=user.is_active,
            is_profile_complete=profile.is_profile_complete if profile else False,
            created_at=user.created_at,
            last_seen_at=user.last_seen_at,
            height=profile.height if profile else None,
            weight=profile.weight if profile else None,
            body_type=profile.body_type if profile else None,
            relationship_status=profile.relationship_status if profile else None,
        )
    )


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
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


@router.post("/register/init", response_model=RegisterInitResponse)
@limiter.limit("5/minute")
async def register_init(
    request: Request,
    body: RegisterInitRequest,
    session: AsyncSession = Depends(get_session),
):
    existing = await get_user_by_email(session, body.email)

    if existing and existing.registration_status == "onboarding_complete":
        # Email exists and fully registered — return same response (no code sent)
        return RegisterInitResponse(
            message="If this email is new, a verification code has been sent.",
            email=body.email,
            expires_in=300,
        )

    # New email OR mid-registration — send (or resend) verification code
    code = generate_verification_code()
    await redis.store_verification_code(body.email, code, ttl=300)
    await send_verification_code(body.email, code)

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
    except Exception:
        pass  # Redis failure shouldn't block registration

    return RegisterInitResponse(
        message="If this email is new, a verification code has been sent.",
        email=body.email,
        expires_in=300,
    )


@router.post("/register/verify", response_model=RegisterVerifyResponse)
@limiter.limit("10/minute")
async def register_verify(
    request: Request,
    body: RegisterVerifyRequest,
    session: AsyncSession = Depends(get_session),
):
    await redis.verify_code_with_attempts(body.email, body.code)

    existing = await get_user_by_email(session, body.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        registration_status="email_verified",
        token_version=1,
        referral_code=generate_referral_code(),
    )
    session.add(user)
    await session.flush()
    
    await create_user_profile(user, session)
    await create_user_settings(user, session)
    
    if hasattr(body, 'referral_code') and body.referral_code:
        result = await session.execute(
            select(User).where(User.referral_code == body.referral_code)
        )
        referred_by_user = result.scalar_one_or_none()
        if referred_by_user:
            user.referred_by = referred_by_user.id
            await session.flush()
    
    await session.commit()
    await redis.delete_verification_code(body.email)
    
    access_token = create_access_token(str(user.id), user.token_version)
    refresh_token = create_refresh_token(str(user.id), user.token_version)
    await redis.store_refresh_token(refresh_token, str(user.id))
    
    return RegisterVerifyResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user_id=user.id
    )


@router.post("/register/complete", response_model=LoginResponse)
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
    
    return await build_login_response(current_user, session)


@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await get_user_by_email(session, body.email)

    if not user or not user.password_hash:
        # Run dummy bcrypt to normalize timing (prevents email enumeration via timing)
        DUMMY_HASH = "$2b$12$BmmIBosrql7pMrMRmgS9ielrTKurq90SXrUIRwPgKbA.36rRDh0ie"
        verify_password(body.password, DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    return await build_login_response(user, session)


@router.post("/google", response_model=LoginResponse)
@limiter.limit("10/minute")
async def google_auth(
    request: Request,
    body: GoogleLoginRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        id_info = google_id_token.verify_oauth2_token(
            body.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token.",
        )

    google_id = id_info["sub"]
    email = id_info.get("email")
    name = id_info.get("name") or email.split("@")[0]

    result = await session.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user:
        user = await get_user_by_email(session, email)
        if user:
            user.google_id = google_id
        else:
            user = User(
                email=email,
                google_id=google_id,
                registration_status="email_verified",
                token_version=1,
                referral_code=generate_referral_code(),
            )
            session.add(user)
            await session.flush()
            
            profile = await create_user_profile(user, session)
            profile.name = name
            await create_user_settings(user, session)
            
            await session.commit()

    await session.commit()
    await session.refresh(user)

    return await build_login_response(user, session)


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

    user = await get_user_with_profile(session, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated.",
        )

    await redis.revoke_refresh_token(body.refresh_token)

    new_access = create_access_token(str(user.id), user.token_version)
    new_refresh = create_refresh_token(str(user.id), user.token_version)
    await redis.store_refresh_token(new_refresh, str(user.id))

    return RefreshTokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer"
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def logout(request: Request, body: LogoutRequest):
    await redis.revoke_refresh_token(body.refresh_token)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    body: dict,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    old_password = body.get("old_password")
    new_password = body.get("new_password")
    
    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="Missing passwords.")
    
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    
    if not current_user.password_hash:
        raise HTTPException(status_code=400, detail="This account uses Google login.")
    
    if not verify_password(old_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect old password.")
    
    current_user.password_hash = hash_password(new_password)
    current_user.token_version += 1
    
    await redis.revoke_all_user_tokens(str(current_user.id))
    
    await session.commit()
    
    return None


@router.post("/password-reset", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("3/minute")
async def password_reset(
    request: Request,
    body: PasswordResetRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await get_user_by_email(session, body.email)
    if not user:
        return None
    
    code = generate_verification_code()
    await redis.store_verification_code(body.email, code, ttl=300)
    
    return None


@router.post("/password-reset/verify", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def password_reset_verify(
    request: Request,
    body: PasswordResetVerifyRequest,
    session: AsyncSession = Depends(get_session),
):
    await redis.verify_code_with_attempts(body.email, body.code)

    user = await get_user_by_email(session, body.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    
    await redis.revoke_all_user_tokens(str(user.id))
    await redis.delete_verification_code(body.email)
    
    await session.commit()
    
    return None


@router.get("/health")
async def auth_health():
    redis_ok = await redis.health_check()
    return {
        "status": "healthy" if redis_ok else "degraded",
        "redis": "connected" if redis_ok else "disconnected",
    }