from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, update
from uuid import UUID
from datetime import datetime

from app.db.session import get_session
from app.core.deps import get_admin_user
from app.core.limiter import limiter
from sqlalchemy.orm import selectinload
from app.models.user import User
from app.models.ticket import Ticket
from app.schemas.admin import AdminTicketResponse, AdminTicketUpdate
from app.schemas.ticket import TicketListResponse

from app.core.logging import get_logger
from app.core.timezone import utcnow
from app.services.admin_log_service import log_admin_action

logger = get_logger("admin_tickets")

router = APIRouter(prefix="/admin/tickets", tags=["admin"])


@router.get("", response_model=TicketListResponse)
@limiter.limit("60/minute")
async def admin_list_tickets(
    request: Request,
    status_filter: str = Query(None, pattern="^(open|in_progress|closed)$"),
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: List all tickets (optionally filtered by status)"""
    
    query = select(Ticket).order_by(Ticket.created_at.desc())
    
    if status_filter:
        query = query.where(Ticket.status == status_filter)
    
    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query)
    
    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    tickets = result.scalars().all()
    
    # Get user info for each ticket
    response_tickets = []
    for ticket in tickets:
        user_result = await session.execute(
            select(User).options(selectinload(User.profile)).where(User.id == ticket.user_id)
        )
        user = user_result.scalar_one_or_none()
        
        response_tickets.append(AdminTicketResponse(
            id=ticket.id,
            user_id=ticket.user_id,
            user_name=user.profile.name if user else "Deleted User",
            user_email=user.email if user else "deleted@example.com",
            subject=ticket.subject,
            message=ticket.message,
            status=ticket.status,
            admin_response=ticket.admin_response,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at
        ))
    
    return TicketListResponse(
        tickets=response_tickets,
        total=total or 0,
        next_offset=offset + limit if offset + limit < (total or 0) else None
    )


@router.get("/{ticket_id}", response_model=AdminTicketResponse)
@limiter.limit("60/minute")
async def admin_get_ticket(
    request: Request,
    ticket_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Get ticket details"""
    
    result = await session.execute(
        select(Ticket).where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    user_result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == ticket.user_id)
    )
    user = user_result.scalar_one_or_none()
    
    return AdminTicketResponse(
        id=ticket.id,
        user_id=ticket.user_id,
        user_name=user.profile.name if user else "Deleted User",
        user_email=user.email if user else "deleted@example.com",
        subject=ticket.subject,
        message=ticket.message,
        status=ticket.status,
        admin_response=ticket.admin_response,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at
    )


@router.patch("/{ticket_id}", response_model=AdminTicketResponse)
@limiter.limit("30/minute")
async def admin_update_ticket(
    request: Request,
    ticket_id: UUID,
    body: AdminTicketUpdate,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Respond to ticket and update status"""
    
    result = await session.execute(
        select(Ticket).where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    if body.status:
        ticket.status = body.status
    if body.admin_response is not None:
        ticket.admin_response = body.admin_response
        ticket.status = "closed" if not body.status else ticket.status
    
    ticket.updated_at = utcnow()
    await session.commit()
    await log_admin_action(str(admin.id), "ticket_update", "ticket", ticket.id, request, session)
    
    user_result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == ticket.user_id)
    )
    user = user_result.scalar_one_or_none()
    
    return AdminTicketResponse(
        id=ticket.id,
        user_id=ticket.user_id,
        user_name=user.profile.name if user else "Deleted User",
        user_email=user.email if user else "deleted@example.com",
        subject=ticket.subject,
        message=ticket.message,
        status=ticket.status,
        admin_response=ticket.admin_response,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at
    )


@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def admin_delete_ticket(
    request: Request,
    ticket_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Admin: Delete a ticket"""
    
    result = await session.execute(
        select(Ticket).where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    await log_admin_action(str(admin.id), "ticket_delete", "ticket", ticket.id, request, session)
    await session.delete(ticket)
    await session.commit()