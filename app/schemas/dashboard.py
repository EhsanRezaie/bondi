from pydantic import BaseModel


class DashboardOverviewResponse(BaseModel):
    total_users: int
    active_today: int
    new_users_today: int
    new_users_this_week: int
    premium_users: int
    premium_percentage: float
    total_swipes_today: int
    total_matches_today: int
    total_messages_today: int
    pending_photos: int
    pending_reports: int
    open_tickets: int


class ChartDataPoint(BaseModel):
    date: str
    value: int


class UserGrowthResponse(BaseModel):
    labels: list[str]
    new_users: list[int]
    active_users: list[int]


class ActivityStatsResponse(BaseModel):
    labels: list[str]
    swipes: list[int]
    matches: list[int]
    messages: list[int]


class ReportStatsResponse(BaseModel):
    pending: int
    reviewed: int
    action_taken: int


class TicketStatsResponse(BaseModel):
    open: int
    in_progress: int
    closed: int
