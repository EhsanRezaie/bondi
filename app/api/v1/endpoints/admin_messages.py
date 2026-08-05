# app/api/v1/endpoints/admin_messages.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from datetime import datetime  

from app.db.session import get_session
from app.models.user import User
from app.models.message import Message
from app.models.report import Report  
from app.core.deps import get_admin_user
from app.schemas.message import MessageResponse
from app.schemas.admin import (
    AdminMessageDecryptResponse,
    AdminMessageDeleteResponse,
    AdminReportedMessageResponse,
)
from app.services.chat_service import get_message_for_admin

from app.core.logging import get_logger

logger = get_logger("admin_messages")

router = APIRouter(prefix="/admin/messages", tags=["admin"])


@router.get("/{message_id}/decrypt", response_model=AdminMessageDecryptResponse)
async def admin_decrypt_message(
    message_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin_user: User = Depends(get_admin_user),
) -> dict:
    """
    Decrypt a message for admin moderation.
    Only accessible by admin users.
    """
    message, decrypted_content = await get_message_for_admin(session, message_id)
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    return {
        "message_id": str(message.id),
        "sender_id": str(message.sender_id),
        "receiver_id": str(message.receiver_id),
        "match_id": str(message.match_id) if message.match_id else None,
        "content": decrypted_content,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "is_accepted": message.is_accepted,
    }


@router.delete("/{message_id}", response_model=AdminMessageDeleteResponse)
async def admin_delete_message(
    message_id: UUID,
    reason: str = "Violates terms of service",
    session: AsyncSession = Depends(get_session),
    admin_user: User = Depends(get_admin_user),
) -> dict:
    """
    Admin delete a message (for moderation).
    """
    result = await session.execute(
        select(Message).where(Message.id == message_id)
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Admin deletion - mark as deleted for all
    message.is_deleted_for_all = True
    message.deleted_at = datetime.utcnow()  # ✅ Now works with import
    message._content = f"[Deleted by admin: {reason}]"
    
    await session.commit()
    
    return {
        "message": "Message deleted by admin",
        "message_id": str(message_id),
        "reason": reason,
    }


@router.get("/reports/{report_id}/message", response_model=AdminReportedMessageResponse)
async def admin_view_reported_message(
    report_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin_user: User = Depends(get_admin_user),
) -> dict:
    """
    View the message associated with a report.
    """
    result = await session.execute(
        select(Report).where(Report.id == report_id)
    )
    report = result.scalar_one_or_none()
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    if not report.message_id:
        raise HTTPException(status_code=400, detail="Report does not have an associated message")
    
    message, decrypted_content = await get_message_for_admin(session, report.message_id)
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    return {
        "report_id": str(report_id),
        "message_id": str(message.id),
        "content": decrypted_content,
        "sender_id": str(message.sender_id),
        "receiver_id": str(message.receiver_id),
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "report_reason": report.reason,
        "report_description": report.description,
    }