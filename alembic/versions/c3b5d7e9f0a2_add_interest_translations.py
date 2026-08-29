"""add translations column to interests

Revision ID: c3b5d7e9f0a2
Revises: b2a4c6d8e0f1
Create Date: 2026-08-29 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3b5d7e9f0a2'
down_revision: Union[str, Sequence[str], None] = 'b2a4c6d8e0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add per-language display translations to interests (JSON)."""
    op.add_column('interests', sa.Column('translations', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('interests', 'translations')
