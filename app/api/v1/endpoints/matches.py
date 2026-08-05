from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.db.session import get_session
from app.models.user import User
from app.models.match import Match
from app.models.photo import Photo
from app.models.message import Message
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.schemas.match import MatchResponse, MatchListResponse, MatchDetailResponse, MatchUserResponse, LastMessageResponse
from app.services.notification_service import NotificationService
from app.services.photo_service import PhotoService

from app.core.logging import get_logger

logger = get_logger("matches")

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=MatchListResponse)
@limiter.limit("60/minute")
async def get_matches(
    request: Request,
    limit: int = Query(50, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MatchListResponse:
    """
    Get all active matches for current user.
    Returns matches sorted by most recent message or match date.
    """

    query = select(Match).options(
        selectinload(Match.user1).selectinload(User.profile),
        selectinload(Match.user1).selectinload(User.photos),
        selectinload(Match.user2).selectinload(User.profile),
        selectinload(Match.user2).selectinload(User.photos),
    ).where(
        or_(
            Match.user1_id == current_user.id,
            Match.user2_id == current_user.id
        ),
        Match.is_active == True
    ).order_by(Match.matched_at.desc())

    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query)

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    matches = result.scalars().all()

    # Resolve online status for all matched users (bulk Redis lookup)
    online_map = {}
    if matches:
        other_ids = [
            (m.user2_id if m.user1_id == current_user.id else m.user1_id).__str__()
            for m in matches
        ]
        if other_ids:
            from app.services.websocket_manager import websocket_manager
            import app.core.redis as redis_module
            online_map = await websocket_manager.get_online_status_bulk(
                other_ids, redis_module.redis_client
            )

    # Single query for all last messages (eliminates N+1)
    last_messages = {}
    if matches:
        match_ids = [m.id for m in matches]
        last_msg_query = (
            select(Message)
            .where(
                Message.match_id.in_(match_ids),
                Message.is_deleted_for_all == False
            )
            .order_by(Message.match_id, Message.sent_at.desc())
            .distinct(Message.match_id)
        )
        last_msg_result = await session.execute(last_msg_query)
        for msg in last_msg_result.scalars().all():
            last_messages[msg.match_id] = msg

    match_responses = []
    for match in matches:
        if match.user1_id == current_user.id:
            other_user = match.user2
        else:
            other_user = match.user1

        main_photo = next((p for p in other_user.photos if p.is_main and p.status == "approved"), None) if other_user.photos else None
        main_photo_url = await PhotoService.get_photo_url(main_photo.url, main_photo.status) if main_photo else None

        last_msg = last_messages.get(match.id)
        last_message = LastMessageResponse(
            content=last_msg.content,
            sent_at=last_msg.sent_at,
            is_read=last_msg.is_read
        ) if last_msg else None

        match_responses.append(MatchResponse(
            id=match.id,
            matched_at=match.matched_at,
            user=MatchUserResponse(
                id=other_user.id,
                name=other_user.profile.name,
                age=other_user.profile.age,
                main_photo_url=main_photo_url,
                is_online=online_map.get(str(other_user.id), False),
                last_seen_at=other_user.last_seen_at,
            ),
            last_message=last_message
        ))

    return MatchListResponse(
        matches=match_responses,
        total=total or 0
    )


@router.get("/{match_id}", response_model=MatchDetailResponse)
@limiter.limit("60/minute")
async def get_match_detail(
    request: Request,
    match_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MatchDetailResponse:
    """
    Get detailed information about a specific match.
    """

    query = select(Match).options(
        selectinload(Match.user1).selectinload(User.profile),
        selectinload(Match.user1).selectinload(User.photos),
        selectinload(Match.user2).selectinload(User.profile),
        selectinload(Match.user2).selectinload(User.photos),
    ).where(
        Match.id == match_id,
        Match.is_active == True,
        or_(
            Match.user1_id == current_user.id,
            Match.user2_id == current_user.id
        )
    )

    result = await session.execute(query)
    match = result.scalar_one_or_none()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Match not found"
        )

    user1_photo = next((p for p in match.user1.photos if p.is_main and p.status == "approved"), None) if match.user1.photos else None
    user1_photo_url = await PhotoService.get_photo_url(user1_photo.url, user1_photo.status) if user1_photo else None
    user2_photo = next((p for p in match.user2.photos if p.is_main and p.status == "approved"), None) if match.user2.photos else None
    user2_photo_url = await PhotoService.get_photo_url(user2_photo.url, user2_photo.status) if user2_photo else None

    return MatchDetailResponse(
        id=match.id,
        matched_at=match.matched_at,
        user1=MatchUserResponse(
            id=match.user1.id,
            name=match.user1.profile.name,
            age=match.user1.profile.age,
            main_photo_url=user1_photo_url,
        ),
        user2=MatchUserResponse(
            id=match.user2.id,
            name=match.user2.profile.name,
            age=match.user2.profile.age,
            main_photo_url=user2_photo_url,
        ),
        is_active=match.is_active
    )
