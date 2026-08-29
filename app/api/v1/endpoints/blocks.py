from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from uuid import UUID
from datetime import datetime, timezone

from app.db.session import get_session
from app.models.user import User
from app.models.block import Block
from app.core.deps import get_current_user, get_current_user_id
from app.core.limiter import limiter
from app.schemas.search import BlockResponse
from app.services.websocket_manager import websocket_manager
from app.services.photo_service import PhotoService
import app.core.redis as redis_module

from app.core.logging import get_logger

logger = get_logger("blocks")

router = APIRouter(prefix="/blocks", tags=["blocks"])


@router.post("/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def block_user(
    request: Request,
    user_id: UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> None:
    """
    Block a user.
    Blocked users won't appear in discover or search.
    A realtime `blocked` event is published to both users so any open chat
    shows "This conversation is over."
    """
    
    if user_id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot block yourself"
        )
    
    # Check if target user exists
    result = await session.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    target_user = result.scalar_one_or_none()
    
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check if already blocked
    existing = await session.execute(
        select(Block).where(
            Block.blocker_id == current_user_id,
            Block.blocked_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already blocked"
        )
    
    # Create block
    block = Block(
        blocker_id=current_user_id,
        blocked_id=user_id,
    )
    session.add(block)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        logger.warning("block_duplicate", blocker_id=str(current_user_id), blocked_id=str(user_id), error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already blocked",
        )
    await session.commit()

    # End any active chat between the pair (conversation is over).
    await _end_blocked_chat(session, current_user_id, user_id)

    # Notify both users so their open chats flip to the blocked/ended state.
    payload = {"type": "blocked", "data": {"user_id": str(user_id)}}
    background_tasks.add_task(
        _background_personal_send,
        user_id=str(current_user_id),
        message=payload,
    )
    background_tasks.add_task(
        _background_personal_send,
        user_id=str(user_id),
        message=payload,
    )


async def _end_blocked_chat(
    session: AsyncSession,
    user_a: UUID,
    user_b: UUID,
) -> None:
    """Mark any active chat between user_a and user_b as ended."""
    from app.models.chat import Chat
    from sqlalchemy import or_
    result = await session.execute(
        select(Chat).where(
            Chat.is_active == True,
            or_(
                (Chat.initiator_id == user_a) & (Chat.recipient_id == user_b),
                (Chat.initiator_id == user_b) & (Chat.recipient_id == user_a),
            ),
        )
    )
    chat = result.scalar_one_or_none()
    if chat and not chat.is_ended:
        chat.is_ended = True
        chat.ended_by = user_a
        chat.ended_at = datetime.now(timezone.utc)
        await session.commit()


async def _background_personal_send(user_id: str, message: dict):
    try:
        await websocket_manager.send_personal_message(user_id, message, redis_module.redis_client)
    except Exception as e:
        logger.error("bg_personal_send_failed", user_id=user_id, error=str(e), exc_info=True)


@router.post("/{user_id}/unblock", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def unblock_user(
    request: Request,
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> None:
    """
    Unblock a user.
    """
    
    result = await session.execute(
        select(Block).where(
            Block.blocker_id == current_user_id,
            Block.blocked_id == user_id,
        )
    )
    block = result.scalar_one_or_none()
    
    if not block:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Block not found"
        )
    
    await session.delete(block)
    await session.commit()


@router.get("", response_model=list[BlockResponse])
@limiter.limit("30/minute")
async def list_blocks(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[BlockResponse]:
    """
    List all users blocked by current user.
    """
    
    # Count total
    count_query = select(func.count()).select_from(
        select(Block).where(Block.blocker_id == current_user.id).subquery()
    )
    total = await session.scalar(count_query)
    
    result = await session.execute(
        select(Block, User)
        .options(
            selectinload(User.profile),
            selectinload(User.photos),
        )
        .join(User, Block.blocked_id == User.id)
        .where(Block.blocker_id == current_user.id)
        .order_by(Block.created_at.desc(), Block.id.desc())
        .offset(offset)
        .limit(limit)
    )
    
    blocks = []
    for block, user in result:
        profile = user.profile
        name = profile.name if profile and profile.name else None
        age = profile.age if profile else None

        main_photo_url = None
        if user.photos:
            approved = sorted(
                [p for p in user.photos if p.status == "approved"],
                key=lambda p: p.order,
            )
            if approved:
                first = approved[0]
                main_photo_url = await PhotoService.get_photo_url(first.url, first.status)

        blocks.append(BlockResponse(
            id=block.id,
            blocked_user_id=user.id,
            blocked_user_name=name,
            blocked_user_age=age,
            main_photo_url=main_photo_url,
            blocked_at=block.created_at.isoformat(),
        ))
    
    return blocks