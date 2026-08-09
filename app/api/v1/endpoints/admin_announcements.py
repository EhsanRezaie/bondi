from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone

from app.db.session import get_session
from app.core.deps import get_admin_user
from app.core.limiter import limiter
from sqlalchemy.orm import selectinload
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.notification import Notification
from app.schemas.admin import (
    AdminMessageRequest,
    AdminMessageResponse,
    AdminAnnouncementRequest,
    AdminAnnouncementResponse
)

from app.core.logging import get_logger
from app.services.admin_log_service import log_admin_action

logger = get_logger("admin_announcements")

router = APIRouter(prefix="/admin/announcements", tags=["admin"])


@router.post("", response_model=AdminAnnouncementResponse)
@limiter.limit("10/minute")
async def admin_send_announcement(
    request: Request,
    body: AdminAnnouncementRequest,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Send announcement to all active users (or premium only)"""
    
    # Build query for target users
    query = select(User).options(selectinload(User.profile)).where(User.is_active == True)
    
    if body.to_premium_only:
        query = query.join(User.profile).where(UserProfile.premium_until > datetime.now(timezone.utc))
    
    result = await session.execute(query)
    users = result.scalars().all()
    
    if not users:
        return AdminAnnouncementResponse(
            success=True,
            message="No users to send announcement to",
            recipient_count=0
        )
    
    # Create notifications for all users
    notifications = []
    for user in users:
        notifications.append(Notification(
            user_id=user.id,
            type="system",
            title=body.title,
            body=body.message,
            data={"is_announcement": True, "to_premium_only": body.to_premium_only},
            is_read=False
        ))
    
    session.add_all(notifications)
    await session.commit()
    await log_admin_action(str(admin.id), "announcement_send", "system", admin.id, request, session)

    return AdminAnnouncementResponse(
        success=True,
        message=f"Announcement sent to {len(users)} users",
        recipient_count=len(users)
    )


@router.post("/test", response_model=AdminMessageResponse)
@limiter.limit("10/minute")
async def admin_send_test_announcement(
    request: Request,
    body: AdminMessageRequest,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Send test announcement to admin only"""
    
    notification = Notification(
        user_id=admin.id,
        type="system",
        title=f"[TEST] {body.title}",
        body=body.message,
        data={"is_announcement": True, "is_test": True},
        is_read=False
    )
    session.add(notification)
    await session.commit()
    await log_admin_action(str(admin.id), "announcement_test", "system", admin.id, request, session)

    return AdminMessageResponse(
        success=True,
        message="Test announcement sent to admin",
        user_id=admin.id,
        user_name=admin.profile.name if admin.profile else None
    )