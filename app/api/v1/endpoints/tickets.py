from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.db.session import get_session
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.models.ticket import Ticket
from app.models.ticket_message import TicketMessage
from app.schemas.ticket import TicketCreate, TicketResponse, TicketListResponse, TicketMessageCreate

from app.core.logging import get_logger
from app.core.timezone import utcnow

logger = get_logger("tickets")

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def create_ticket(
    request: Request,
    body: TicketCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Submit a support ticket"""

    ticket = Ticket(
        user_id=current_user.id,
        subject=body.subject,
        message=body.message,
        status="open"
    )
    session.add(ticket)
    await session.flush()

    # Seed the initial message as the first entry in the conversation thread
    session.add(TicketMessage(
        ticket_id=ticket.id,
        sender_type="user",
        sender_user_id=current_user.id,
        content=body.message,
    ))
    await session.commit()

    return await _build_ticket_response(session, ticket)


@router.get("", response_model=TicketListResponse)
@limiter.limit("30/minute")
async def get_my_tickets(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get user's own tickets"""

    query = select(Ticket).where(
        Ticket.user_id == current_user.id
    ).order_by(Ticket.created_at.desc(), Ticket.id.desc())

    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query)

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    tickets = result.scalars().all()

    return TicketListResponse(
        tickets=[await _build_ticket_response(session, t, include_messages=False) for t in tickets],
        total=total or 0,
        next_offset=offset + limit if offset + limit < (total or 0) else None
    )


@router.get("/{ticket_id}", response_model=TicketResponse)
@limiter.limit("30/minute")
async def get_ticket(
    request: Request,
    ticket_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get ticket details (user can only see their own)"""

    result = await session.execute(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.user_id == current_user.id
        )
    )
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return await _build_ticket_response(session, ticket)


@router.post("/{ticket_id}/messages", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def reply_to_ticket(
    request: Request,
    ticket_id: UUID,
    body: TicketMessageCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Reply to one of the user's own tickets. Replying to a closed ticket reopens it."""

    result = await session.execute(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.user_id == current_user.id
        )
    )
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # A user reply to a closed ticket reopens it so the conversation can continue
    if ticket.status == "closed":
        ticket.status = "open"
    ticket.updated_at = utcnow()

    session.add(TicketMessage(
        ticket_id=ticket.id,
        sender_type="user",
        sender_user_id=current_user.id,
        content=body.content,
    ))
    await session.commit()

    return await _build_ticket_response(session, ticket)


async def _build_ticket_response(
    session: AsyncSession,
    ticket: Ticket,
    include_messages: bool = True,
) -> TicketResponse:
    """Assemble the user ticket response, optionally with the conversation thread."""
    messages = await _load_messages(session, ticket.id) if include_messages else []
    return TicketResponse(
        id=ticket.id,
        user_id=ticket.user_id,
        subject=ticket.subject,
        message=ticket.message,
        status=ticket.status,
        admin_response=ticket.admin_response,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        messages=messages,
    )


async def _load_messages(session: AsyncSession, ticket_id: UUID) -> list:
    """Load all messages for a ticket ordered oldest-first."""
    result = await session.execute(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket_id)
        .order_by(TicketMessage.created_at.asc(), TicketMessage.id.asc())
    )
    return result.scalars().all()
