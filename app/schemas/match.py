from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class MatchUserResponse(BaseModel):
    """User info within match response"""
    id: UUID
    name: str
    age: int
    main_photo_url: Optional[str] = None
    is_online: bool = False
    last_seen_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class LastMessageResponse(BaseModel):
    """Last message preview in match list"""
    content: str
    sent_at: datetime
    is_read: bool


class MatchResponse(BaseModel):
    """Response schema for a single match"""
    id: UUID
    matched_at: datetime
    user: MatchUserResponse
    last_message: Optional[LastMessageResponse] = None
    
    class Config:
        from_attributes = True


class MatchListResponse(BaseModel):
    """Response for matches list"""
    matches: List[MatchResponse]
    total: int


class MatchDetailResponse(BaseModel):
    """Detailed match response"""
    id: UUID
    matched_at: datetime
    user1: MatchUserResponse
    user2: MatchUserResponse
    is_active: bool
    
    class Config:
        from_attributes = True


class WebSocketMessage(BaseModel):
    """WebSocket message format"""
    type: str  # 'new_match', 'new_message', 'match_ended'
    data: dict