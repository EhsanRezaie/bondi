# app/api/v1/endpoints/conversations.py
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func, or_, and_, case
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import Optional

from app.db.session import get_session
from app.models.user import User
from app.models.match import Match
from app.models.message import Message
from app.models.photo import Photo
from app.models.block import Block
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.schemas.conversation import (
    ConversationResponse,
    ConversationListResponse,
    ConversationUserResponse,
    ConversationLastMessage,
)
from app.services.photo_service import PhotoService
from app.services.websocket_manager import websocket_manager
import app.core.redis as redis_module

from app.core.logging import get_logger

logger = get_logger("conversations")

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _main_photo_url(session: AsyncSession, photo: Photo) -> Optional[str]:
    if not photo:
        return None
    return await PhotoService.get_photo_url(photo.url, photo.status)


async def _load_user_with_media(
    session: AsyncSession, user_ids: list[UUID]
) -> dict[UUID, User]:
    if not user_ids:
        return {}
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.photos),
        )
        .where(User.id.in_(user_ids), User.is_active == True)
    )
    return {u.id: u for u in result.scalars().all()}


async def _last_message_for_condition(
    session: AsyncSession,
    user_id: UUID,
    *,
    match_id: Optional[UUID] = None,
    other_id: Optional[UUID] = None,
):
    """Fetch the most recent visible message for a match OR an unmatched pair."""
    conds = [Message.is_deleted_for_all == False]
    if match_id is not None:
        conds.append(Message.match_id == match_id)
        group_col = Message.match_id
    else:
        conds.append(Message.match_id.is_(None))
        conds.append(
            or_(
                and_(Message.sender_id == user_id, Message.receiver_id == other_id),
                and_(Message.sender_id == other_id, Message.receiver_id == user_id),
            )
        )
        group_col = Message.sender_id
    conds.append(
        or_(Message.is_deleted_for_sender == False, Message.sender_id != user_id)
    )
    conds.append(
        or_(Message.is_deleted_for_receiver == False, Message.receiver_id != user_id)
    )
    query = (
        select(Message)
        .where(*conds)
        .order_by(group_col, Message.sent_at.desc())
        .distinct(group_col)
        .limit(1)
    )
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def _unread_count_for_condition(
    session: AsyncSession,
    user_id: UUID,
    *,
    match_id: Optional[UUID] = None,
    other_id: Optional[UUID] = None,
) -> int:
    conds = [
        Message.receiver_id == user_id,
        Message.is_read == False,
        Message.is_deleted_for_all == False,
        Message.is_deleted_for_receiver == False,
    ]
    if match_id is not None:
        conds.append(Message.match_id == match_id)
    else:
        conds.append(Message.match_id.is_(None))
        conds.append(
            or_(
                and_(Message.sender_id == user_id, Message.receiver_id == other_id),
                and_(Message.sender_id == other_id, Message.receiver_id == user_id),
            )
        )
    total = await session.scalar(select(func.count()).select_from(Message).where(*conds))
    return total or 0


@router.get("", response_model=ConversationListResponse)
@limiter.limit("60/minute")
async def get_conversations(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ConversationListResponse:
    """
    Get all conversations for the current user: active matches AND
    unmatched chat threads (messages with no match). Sorted by latest
    activity (last message / match date), newest first.
    """
    user_id = current_user.id

    # ── Blocked users (either direction) ──────────────────────────────
    blocked_ids = set(
        (await session.execute(select(Block.blocked_id).where(Block.blocker_id == user_id))).scalars().all()
    ) | set(
        (await session.execute(select(Block.blocker_id).where(Block.blocked_id == user_id))).scalars().all()
    )

    # ── Active matches ────────────────────────────────────────────────
    matches_result = await session.execute(
        select(Match)
        .options(
            selectinload(Match.user1).selectinload(User.profile),
            selectinload(Match.user1).selectinload(User.photos),
            selectinload(Match.user2).selectinload(User.profile),
            selectinload(Match.user2).selectinload(User.photos),
        )
        .where(
            or_(Match.user1_id == user_id, Match.user2_id == user_id),
            Match.is_active == True,
        )
        .order_by(Match.matched_at.desc())
    )
    matches = matches_result.scalars().all()

    match_conversations: list[dict] = []
    matched_other_ids: set[UUID] = set()
    for match in matches:
        other_user = match.user2 if match.user1_id == user_id else match.user1
        if other_user is None:
            continue
        matched_other_ids.add(other_user.id)
        last_msg = await _last_message_for_condition(
            session, user_id, match_id=match.id
        )
        unread = await _unread_count_for_condition(session, user_id, match_id=match.id)
        match_conversations.append({
            "id": match.id,
            "kind": "match",
            "user": other_user,
            "last_message": last_msg,
            "unread_count": unread,
            "updated_at": (last_msg.sent_at if last_msg else match.matched_at),
            "is_accepted": True,
        })

    # ── Unmatched threads (messages without a match) ──────────────────
    other_id_expr = case(
        (Message.sender_id == user_id, Message.receiver_id),
        else_=Message.sender_id,
    )

    unmatched_q = (
        select(other_id_expr.label("other_id"))
        .where(
            or_(Message.sender_id == user_id, Message.receiver_id == user_id),
            Message.match_id.is_(None),
            Message.is_deleted_for_all == False,
            or_(Message.is_deleted_for_sender == False, Message.sender_id != user_id),
            or_(Message.is_deleted_for_receiver == False, Message.receiver_id != user_id),
        )
        .group_by(other_id_expr)
    )
    unmatched_other_ids = {
        row.other_id
        for row in (await session.execute(unmatched_q)).all()
        if row.other_id is not None
    }
    # Exclude users we're already matched with (covered by the match thread)
    unmatched_other_ids -= matched_other_ids

    unmatched_conversations: list[dict] = []
    if unmatched_other_ids:
        users_map = await _load_user_with_media(session, list(unmatched_other_ids))
        for other_id, other_user in users_map.items():
            if other_id not in unmatched_other_ids:
                continue
            last_msg = await _last_message_for_condition(
                session, user_id, other_id=other_id
            )
            if last_msg is None:
                continue
            unread = await _unread_count_for_condition(
                session, user_id, other_id=other_id
            )
            is_accepted = bool(
                await session.scalar(
                    select(Message.is_accepted)
                    .where(
                        Message.match_id.is_(None),
                        Message.is_accepted == True,
                        or_(
                            and_(
                                Message.sender_id == user_id,
                                Message.receiver_id == other_id,
                            ),
                            and_(
                                Message.sender_id == other_id,
                                Message.receiver_id == user_id,
                            ),
                        ),
                    )
                    .limit(1)
                )
            )
            unmatched_conversations.append({
                "id": other_id,
                "kind": "unmatched",
                "user": other_user,
                "last_message": last_msg,
                "unread_count": unread,
                "updated_at": last_msg.sent_at,
                "is_accepted": bool(is_accepted),
            })

    # ── Merge, filter blocked, sort ───────────────────────────────────
    all_convs = match_conversations + unmatched_conversations
    all_convs = [c for c in all_convs if c["user"] is not None and c["user"].id not in blocked_ids]

    all_convs.sort(key=lambda c: c["updated_at"] or c["user"].last_seen_at or c["user"].id, reverse=True)

    total = len(all_convs)
    page = all_convs[offset: offset + limit]

    # ── Presence (online) for the page ────────────────────────────────
    page_user_ids = [c["user"].id for c in page]
    online_map = {}
    if page_user_ids:
        online_map = await websocket_manager.get_online_status_bulk(
            [str(u) for u in page_user_ids], redis_module.redis_client
        )

    conversations = []
    for c in page:
        other = c["user"]
        main_photo = (
            next((p for p in other.photos if p.is_main and p.status == "approved"), None)
            if other.photos
            else None
        )
        main_photo_url = await _main_photo_url(session, main_photo)
        last_msg = c["last_message"]
        last_message = None
        if last_msg is not None:
            content = last_msg.content if last_msg.match_id else last_msg._content
            last_message = ConversationLastMessage(
                content=content,
                message_type=last_msg.message_type,
                is_sent=last_msg.sender_id == user_id,
                is_read=last_msg.is_read,
                sent_at=last_msg.sent_at,
            )
        conversations.append(
            ConversationResponse(
                id=c["id"],
                kind=c["kind"],
                user=ConversationUserResponse(
                    id=other.id,
                    name=other.profile.name if other.profile else "User",
                    age=other.profile.age if other.profile else 0,
                    main_photo_url=main_photo_url,
                    is_online=online_map.get(str(other.id), False),
                    last_seen_at=other.last_seen_at,
                ),
                last_message=last_message,
                is_accepted=c["is_accepted"],
                unread_count=c["unread_count"],
                updated_at=c["updated_at"],
            )
        )

    next_offset = offset + limit if offset + limit < total else None

    return ConversationListResponse(
        conversations=conversations,
        total=total,
        next_offset=next_offset,
    )