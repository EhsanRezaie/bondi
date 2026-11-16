from uuid import UUID

from pydantic import BaseModel


class PromptResponse(BaseModel):
    """A single prompt question, in the language it was requested in.

    Unlike Interest, the `question` text here IS the actual display text
    (already localized server-side) — no client-side translation needed,
    since the same logical prompt exists as separate rows per language.
    """

    id: UUID
    question: str
    category: str | None = None
    language: str

    class Config:
        from_attributes = True
