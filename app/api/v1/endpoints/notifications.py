from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from uuid import UUID

from app.db.session import get_session
from app.core.deps import get_current_user, get_current_user_id
from app.core.limiter import limiter
from app.models.user import User
from app.models.notification import Notification
from app.models.device_token import DeviceToken
from app.schemas.notification import (
    NotificationResponse,
    NotificationListResponse,
    MarkReadRequest,
    DeviceTokenRequest,
    DeviceTokenResponse,
    NotificationCountsResponse,
)

from app.core.logging import get_logger

logger = get_logger("notifications")

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
@limiter.limit("60/minute")
async def get_notifications(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get user's notifications (most recent first)"""
    
    # Query notifications
    filter_stmt = Notification.user_id == current_user.id

    # Count total — plain count, no ORDER BY or pagination
    total = await session.scalar(
        select(func.count(Notification.id)).where(filter_stmt)
    )

    # Paginate (ORDER BY applied only on the data query)
    query = (
        select(Notification)
        .where(filter_stmt)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(query)
    notifications = result.scalars().all()
    
    return NotificationListResponse(
        notifications=[NotificationResponse.model_validate(n) for n in notifications],
        total=total or 0,
        next_offset=offset + limit if offset + limit < total else None
    )


@router.get("/counts", response_model=NotificationCountsResponse)
@limiter.limit("60/minute")
async def get_notification_counts(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get unread notification counts by type for badge display"""
    filter_stmt = Notification.user_id == current_user.id

    # Total unread count
    total = await session.scalar(
        select(func.count(Notification.id)).where(
            filter_stmt, Notification.is_read == False
        )
    )

    # Unread counts by type
    from sqlalchemy import case
    type_counts = await session.execute(
        select(Notification.type, func.count(Notification.id)).where(
            filter_stmt, Notification.is_read == False
        ).group_by(Notification.type)
    )
    by_type = {row[0]: row[1] for row in type_counts.all()}

    return NotificationCountsResponse(
        total=total or 0,
        by_type=by_type,
    )


@router.post("/read", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def mark_notifications_read(
    request: Request,
    body: MarkReadRequest,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
):
    """Mark notification(s) as read"""

    await session.execute(
        Notification.__table__.update()
        .where(Notification.id.in_(body.notification_ids))
        .where(Notification.user_id == current_user_id)
        .values(is_read=True)
    )
    await session.commit()


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def delete_notification(
    request: Request,
    notification_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
):
    """Delete a notification"""
    
    result = await session.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user_id
        )
    )
    notification = result.scalar_one_or_none()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    await session.delete(notification)
    await session.commit()


@router.post("/device-token", response_model=DeviceTokenResponse)
@limiter.limit("10/minute")
async def register_device_token(
    request: Request,
    body: DeviceTokenRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Register or update a device token for push notifications."""
    # Check if token already exists for this user
    result = await session.execute(
        select(DeviceToken).where(
            DeviceToken.user_id == current_user.id,
            DeviceToken.token == body.token,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.platform = body.platform
        await session.flush()
        token_obj = existing
    else:
        token_obj = DeviceToken(
            user_id=current_user.id,
            token=body.token,
            platform=body.platform,
        )
        session.add(token_obj)
        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            logger.warning("device_token_duplicate", user_id=str(current_user.id), error=str(e), exc_info=True)
            existing = await session.execute(
                select(DeviceToken).where(
                    DeviceToken.user_id == current_user.id,
                    DeviceToken.token == body.token,
                )
            )
            token_obj = existing.scalar_one_or_none()
            if token_obj:
                token_obj.platform = body.platform
                await session.flush()
            else:
                raise

    return DeviceTokenResponse(
        id=token_obj.id,
        token=token_obj.token,
        platform=token_obj.platform,
    )


@router.delete("/device-token/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_device_token(
    request: Request,
    token_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Remove a device token (e.g. on logout)."""
    result = await session.execute(
        select(DeviceToken).where(
            DeviceToken.id == token_id,
            DeviceToken.user_id == current_user.id,
        )
    )
    token_obj = result.scalar_one_or_none()
    if not token_obj:
        raise HTTPException(status_code=404, detail="Device token not found")

    await session.delete(token_obj)
    await session.commit()