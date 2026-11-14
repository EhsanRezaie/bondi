from uuid import UUID

from pydantic import BaseModel


class InterestResponse(BaseModel):
    """A single interest, as returned to the client.

    `name` and `category` are stable keys (e.g. 'football', 'sports_fitness') —
    NOT display text. `name_localized` and `category_localized` carry the
    display labels resolved for the requested `language` (falling back to the
    stable key when a translation is missing).
    """

    id: UUID
    name: str
    name_localized: str | None = None
    category: str | None = None
    category_localized: str | None = None
    icon: str | None = None

    class Config:
        from_attributes = True
