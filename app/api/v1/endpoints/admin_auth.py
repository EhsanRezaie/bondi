from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.security import create_admin_token, verify_password

logger = get_logger("admin_auth")

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600


@router.post("/login", response_model=AdminLoginResponse)
@limiter.limit("5/minute")
async def admin_login(
    request: Request,
    body: AdminLoginRequest,
):
    """Admin login with username/password — returns short-lived JWT token."""
    # Verify against ADMIN_USERNAME + ADMIN_PASSWORD_HASH in .env
    admin_username = getattr(settings, "ADMIN_USERNAME", "")
    admin_password_hash = getattr(settings, "ADMIN_PASSWORD_HASH", "")

    if not admin_username or not admin_password_hash:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin login not configured.",
        )

    if body.username != admin_username or not verify_password(body.password, admin_password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials.",
        )

    token = create_admin_token(admin_username)
    logger.info("admin_login_success", admin_id=admin_username)

    return AdminLoginResponse(access_token=token)
