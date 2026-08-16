from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.notification import Notification
from app.models.user import User
from app.models.photo import Photo
from app.core.logging import get_logger

logger = get_logger("services.notification_service")


async def _get_main_photo_url(db: AsyncSession, user_id: UUID) -> str | None:
    """Resolve a user's main approved photo to a loadable URL."""
    from app.services.photo_service import PhotoService

    row = (
        await db.execute(
            select(Photo.url, Photo.status).where(
                Photo.user_id == user_id,
                Photo.is_main == True,
                Photo.status == "approved",
            )
        )
    ).one_or_none()
    if row:
        return await PhotoService.get_photo_url(row[0], row[1])
    return None


def _notification_ws_payload(notification: Notification) -> dict:
    """Shared shape for the personal-channel `new_notification` event."""
    return {
        "type": "new_notification",
        "data": {
            "id": str(notification.id),
            "type": notification.type,
            "title": notification.title,
            "body": notification.body,
            "is_read": notification.is_read,
            "created_at": notification.created_at.isoformat()
            if notification.created_at
            else None,
            "user_id": (notification.data or {}).get("user_id"),
            "match_id": (notification.data or {}).get("match_id"),
            "chat_id": (notification.data or {}).get("chat_id"),
            "name": (notification.data or {}).get("name"),
            "avatar_url": (notification.data or {}).get("avatar_url"),
        },
    }


async def _publish_ws(user_id, notification: Notification):
    """Best-effort publish of a new_notification event to a user's personal
    channel over the existing session socket /ws/stream."""
    try:
        from app.core.redis import redis_client
        from app.services.websocket_manager import websocket_manager

        await websocket_manager.send_personal_message(
            str(user_id), _notification_ws_payload(notification), redis_client
        )
    except Exception as e:
        logger.warning("ws_notification_publish_failed", user_id=str(user_id), error=str(e), exc_info=True)


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        user_id: UUID,
        type: str,
        title: str,
        body: str = None,
        data: dict = None,
        publish: bool = True,
    ) -> Notification:
        """Create a notification for a user. When `publish` is True (default)
        a personal-channel WS event is emitted so live apps update instantly."""
        notification = Notification(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            data=data,
            is_read=False,
        )
        self.db.add(notification)
        await self.db.flush()

        if publish:
            await _publish_ws(user_id, notification)

        return notification

    async def notify_like(
        self,
        liker_id: UUID,
        liked_user_id: UUID,
        liker_name: str,
        liker_age: int,
        liker_photo_url: str | None = None,
    ):
        """Send like notification to the recipient"""
        notification = await self.create(
            user_id=liked_user_id,
            type="like",
            title="Someone liked you!",
            body=f"{liker_name} (age {liker_age}) liked your profile",
            data={
                "user_id": str(liker_id),
                "name": liker_name,
                "avatar_url": liker_photo_url,
            },
        )

        # Push notification
        from app.tasks.notifications import dispatch_push_to_celery
        if not dispatch_push_to_celery(
            user_id=liked_user_id,
            title="Someone liked you!",
            body=f"{liker_name} (age {liker_age}) liked your profile",
            data={"type": "like", "user_id": str(liker_id)},
            image_url=liker_photo_url,
        ):
            from app.services.push_service import PushService
            await PushService.send_to_user(
                user_id=liked_user_id,
                title="Someone liked you!",
                body=f"{liker_name} (age {liker_age}) liked your profile",
                data={"type": "like", "user_id": str(liker_id)},
                db=self.db,
                image_url=liker_photo_url,
            )

    async def notify_liked(self, user_id: UUID, target_user_id: UUID, target_name: str):
        """Send a 'liked' notification to the liker themselves, so the app can
        show an 'I liked' section alongside incoming likes. WS-only: no push to
        self for your own action."""
        avatar = await _get_main_photo_url(self.db, target_user_id)
        await self.create(
            user_id=user_id,
            type="liked",
            title="You liked someone!",
            body=f"You liked {target_name}'s profile",
            data={
                "user_id": str(target_user_id),
                "name": target_name,
                "avatar_url": avatar,
            },
            publish=True,
        )

    async def notify_match(self, user1_id: UUID, user2_id: UUID, match_id: UUID):
        """Send match notification to both users"""
        result1 = await self.db.execute(
            select(User).options(selectinload(User.profile)).where(User.id == user1_id)
        )
        user1 = result1.scalar_one_or_none()
        result2 = await self.db.execute(
            select(User).options(selectinload(User.profile)).where(User.id == user2_id)
        )
        user2 = result2.scalar_one_or_none()

        from app.services.push_service import PushService

        if user1:
            avatar = await _get_main_photo_url(self.db, user2_id)
            await self.create(
                user_id=user1_id,
                type="match",
                title="It's a match!",
                body=f"You matched with {user2.profile.name}! Start chatting now.",
                data={
                    "match_id": str(match_id),
                    "user_id": str(user2_id),
                    "name": user2.profile.name,
                    "avatar_url": avatar,
                },
            )
            from app.tasks.notifications import dispatch_push_to_celery
            if not dispatch_push_to_celery(
                user_id=user1_id,
                title="It's a match!",
                body=f"You matched with {user2.profile.name}!",
                data={"type": "match", "match_id": str(match_id), "user_id": str(user2_id)},
                image_url=avatar,
            ):
                await PushService.send_to_user(
                    user_id=user1_id,
                    title="It's a match!",
                    body=f"You matched with {user2.profile.name}!",
                    data={"type": "match", "match_id": str(match_id), "user_id": str(user2_id)},
                    db=self.db,
                    image_url=avatar,
                )

        if user2:
            avatar = await _get_main_photo_url(self.db, user1_id)
            await self.create(
                user_id=user2_id,
                type="match",
                title="It's a match!",
                body=f"You matched with {user1.profile.name}! Start chatting now.",
                data={
                    "match_id": str(match_id),
                    "user_id": str(user1_id),
                    "name": user1.profile.name,
                    "avatar_url": avatar,
                },
            )
            if not dispatch_push_to_celery(
                user_id=user2_id,
                title="It's a match!",
                body=f"You matched with {user1.profile.name}!",
                data={"type": "match", "match_id": str(match_id), "user_id": str(user1_id)},
                image_url=avatar,
            ):
                await PushService.send_to_user(
                    user_id=user2_id,
                    title="It's a match!",
                    body=f"You matched with {user1.profile.name}!",
                    data={"type": "match", "match_id": str(match_id), "user_id": str(user1_id)},
                    db=self.db,
                    image_url=avatar,
                )

    async def notify_message(self, receiver_id: UUID, sender_id: UUID | None = None, sender_name: str = "Someone", chat_id: UUID | None = None, sender_photo_url: str | None = None):
        """Send message notification when recipient is offline. Push-only by
        design: message notifications are never rendered in the app's
        notification sections (the chat itself is real-time via WS)."""
        if sender_photo_url is None and sender_id is not None:
            sender_photo_url = await _get_main_photo_url(self.db, sender_id)

        await self.create(
            user_id=receiver_id,
            type="message",
            title="New message",
            body=f"{sender_name} sent you a message",
            data={"chat_id": str(chat_id)},
            publish=False,
        )

        from app.tasks.notifications import dispatch_push_to_celery
        if not dispatch_push_to_celery(
            user_id=receiver_id,
            title=sender_name,
            body=f"{sender_name} sent you a message",
            data={"type": "message", "chat_id": str(chat_id), "sender_name": sender_name},
            image_url=sender_photo_url,
        ):
            from app.services.push_service import PushService
            await PushService.send_to_user(
                user_id=receiver_id,
                title=sender_name,
                body=f"{sender_name} sent you a message",
                data={"type": "message", "chat_id": str(chat_id), "sender_name": sender_name},
                db=self.db,
                image_url=sender_photo_url,
            )