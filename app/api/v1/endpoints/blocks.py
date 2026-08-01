from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.db.session import get_session
from app.models.user import User
from app.models.block import Block
from app.core.deps import get_current_user, get_current_user_id
from app.core.limiter import limiter
from app.schemas.search import BlockResponse

from app.core.logging import get_logger

logger = get_logger("blocks")

router = APIRouter(prefix="/blocks", tags=["blocks"])


@router.post("/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def block_user(
    request: Request,
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user_id: UUID = Depends(get_current_user_id),
) -> None:
    """
    Block a user.
    Blocked users won't appear in discover or search.
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
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already blocked",
        )
    await session.commit()


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
        .options(selectinload(User.profile))
        .join(User, Block.blocked_id == User.id)
        .where(Block.blocker_id == current_user.id)
        .order_by(Block.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    
    blocks = []
    for block, user in result:
        blocks.append(BlockResponse(
            id=block.id,
            blocked_user_id=user.id,
            blocked_user_name=user.profile.name,
            blocked_at=block.created_at.isoformat(),
        ))
    
    return blocks