"""add client_id to messages for optimistic-send dedup

Revision ID: d4c6e8f0a1b3
Revises: c3b5d7e9f0a2
Create Date: 2026-08-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4c6e8f0a1b3'
down_revision: Union[str, Sequence[str], None] = 'c3b5d7e9f0a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add client_id so the sender can reconcile optimistic messages."""
    op.add_column('messages', sa.Column('client_id', sa.UUID(), nullable=True))
    op.create_index('ix_messages_client_id', 'messages', ['client_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_messages_client_id', table_name='messages')
    op.drop_column('messages', 'client_id')
