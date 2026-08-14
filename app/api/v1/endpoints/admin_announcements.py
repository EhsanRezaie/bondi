from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone

from app.db.session import get_session
from app.core.deps import get_admin_user, AdminIdentity
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
from app.services.websocket_manager import websocket_manager
from app.core.redis import redis_client

from app.core.logging import get_logger
from app.services.admin_log_service import log_admin_action
from app.tasks.notifications import dispatch_push_to_celery

logger = get_logger("admin_announcements")

router = APIRouter(prefix="/admin/announcements", tags=["admin"])


async def _dispatch_announcement_push(
    session: AsyncSession,
    user_id,
    title: str,
    body: str,
):
    """Send an FCM push for an announcement (celery worker when enabled,
    otherwise inline). Announcements use `type=system` so the app renders them
    in the Announcements section with a compact notification (no avatar)."""
    if not dispatch_push_to_celery(
        user_id=user_id,
        title=title,
        body=body,
        data={"type": "system", "is_announcement": True},
    ):
        from app.services.push_service import PushService
        await PushService.send_to_user(
            user_id=user_id,
            title=title,
            body=body,
            data={"type": "system", "is_announcement": True},
            db=session,
            image_url=None,
        )


@router.post("", response_model=AdminAnnouncementResponse)
@limiter.limit("10/minute")
async def admin_send_announcement(
    request: Request,
    body: AdminAnnouncementRequest,
    session: AsyncSession = Depends(get_session),
    admin: AdminIdentity = Depends(get_admin_user),
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
    
    # Publish WS events for real-time delivery
    for n in notifications:
        try:
            await websocket_manager.send_personal_message(
                str(n.user_id),
                {
                    "type": "new_notification",
                    "data": {
                        "id": str(n.id),
                        "type": n.type,
                        "title": n.title,
                        "body": n.body,
                        "is_read": n.is_read,
                        "created_at": n.created_at.isoformat() if n.created_at else None,
                        "user_id": (n.data or {}).get("user_id"),
                        "match_id": (n.data or {}).get("match_id"),
                        "chat_id": (n.data or {}).get("chat_id"),
                    },
                },
                redis_client,
            )
        except Exception as e:
            logger.warning("ws_announcement_publish_failed", user_id=str(n.user_id), error=str(e))

    # FCM push for background delivery (the only channel when the app is killed)
    for n in notifications:
        try:
            await _dispatch_announcement_push(session, n.user_id, body.title, body.message)
        except Exception as e:
            logger.warning("push_announcement_failed", user_id=str(n.user_id), error=str(e))
    
    await log_admin_action(str(admin.id), "announcement_send", "system", None, request, session)

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
    admin: AdminIdentity = Depends(get_admin_user),
):
    """Admin: Send test announcement to a specific user.

    The admin identity is no longer an app User account, so the recipient is
    resolved explicitly: `body.target_user_id` if given, otherwise the legacy
    `admin@test.com` user (if it still exists) for backward compatibility.
    """
    target = None
    if body.target_user_id:
        result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.id == body.target_user_id)
        )
        target = result.scalar_one_or_none()
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")
    else:
        result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.email == "admin@test.com")
        )
        target = result.scalar_one_or_none()

    if not target:
        return AdminMessageResponse(
            success=False,
            message="No test recipient — pass target_user_id or keep an admin@test.com user",
            user_id=None,
            user_name=None,
        )

    notification = Notification(
        user_id=target.id,
        type="system",
        title=f"[TEST] {body.title}",
        body=body.message,
        data={"is_announcement": True, "is_test": True},
        is_read=False
    )
    session.add(notification)
    await session.commit()
    
    # Publish WS event for real-time delivery
    try:
        await websocket_manager.send_personal_message(
            str(notification.user_id),
            {
                "type": "new_notification",
                "data": {
                    "id": str(notification.id),
                    "type": notification.type,
                    "title": notification.title,
                    "body": notification.body,
                    "is_read": notification.is_read,
                    "created_at": notification.created_at.isoformat() if notification.created_at else None,
                    "user_id": (notification.data or {}).get("user_id"),
                    "match_id": (notification.data or {}).get("match_id"),
                    "chat_id": (notification.data or {}).get("chat_id"),
                },
            },
            redis_client,
        )
    except Exception as e:
        logger.warning("ws_test_announcement_publish_failed", user_id=str(target.id), error=str(e))

    # FCM push for background delivery
    try:
        await _dispatch_announcement_push(session, target.id, f"[TEST] {body.title}", body.message)
    except Exception as e:
        logger.warning("push_test_announcement_failed", user_id=str(target.id), error=str(e))
    
    await log_admin_action(str(admin.id), "announcement_test", "system", target.id, request, session)

    return AdminMessageResponse(
        success=True,
        message="Test announcement sent",
        user_id=target.id,
        user_name=target.profile.name if target.profile else None
    )