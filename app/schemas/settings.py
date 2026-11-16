from pydantic import BaseModel


class UserSettingsResponse(BaseModel):
    hide_last_seen: bool = False
    hide_online_status: bool = False
    push_enabled: bool = True
    like_notifications: bool = True
    match_notifications: bool = True
    message_notifications: bool = True
    language: str = "fa"
    dark_mode: bool = False

    class Config:
        from_attributes = True


class UserSettingsUpdateRequest(BaseModel):
    hide_last_seen: bool | None = None
    hide_online_status: bool | None = None
    push_enabled: bool | None = None
    like_notifications: bool | None = None
    match_notifications: bool | None = None
    message_notifications: bool | None = None
    language: str | None = None
    dark_mode: bool | None = None
