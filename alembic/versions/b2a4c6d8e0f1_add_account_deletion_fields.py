"""add account deletion fields (deleted_at, deleted_reason)

Revision ID: b2a4c6d8e0f1
Revises: 5d6d70f2e5b0
Create Date: 2026-08-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2a4c6d8e0f1'
down_revision: Union[str, Sequence[str], None] = '5d6d70f2e5b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add soft-deletion tracking columns to users."""
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('deleted_reason', sa.String(length=255), nullable=True))
    op.create_index('idx_users_deleted_at', 'users', ['deleted_at'])


def downgrade() -> None:
    op.drop_index('idx_users_deleted_at', table_name='users')
    op.drop_column('users', 'deleted_reason')
    op.drop_column('users', 'deleted_at')
