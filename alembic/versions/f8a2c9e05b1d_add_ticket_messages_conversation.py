"""add ticket messages conversation

Revision ID: f8a2c9e05b1d
Revises: ad655e0fe278
Create Date: 2026-08-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8a2c9e05b1d'
down_revision: Union[str, Sequence[str], None] = 'ad655e0fe278'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ticket_messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('ticket_id', sa.UUID(), nullable=False),
    sa.Column('sender_type', sa.String(length=10), nullable=False),
    sa.Column('sender_user_id', sa.UUID(), nullable=True),
    sa.Column('admin_name', sa.String(length=100), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['sender_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_ticket_messages_ticket', 'ticket_messages', ['ticket_id', 'created_at'], unique=False)

    # Backfill existing tickets into the conversation thread:
    #  - every ticket's original message becomes the first `user` message
    #  - a legacy single admin_response (if present) becomes an `admin` message
    op.execute("""
        INSERT INTO ticket_messages (id, ticket_id, sender_type, sender_user_id, content, created_at)
        SELECT gen_random_uuid(), id, 'user', user_id, message, created_at
        FROM tickets
    """)
    op.execute("""
        INSERT INTO ticket_messages (id, ticket_id, sender_type, content, created_at)
        SELECT gen_random_uuid(), id, 'admin', admin_response, updated_at
        FROM tickets
        WHERE admin_response IS NOT NULL AND btrim(admin_response) <> ''
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_ticket_messages_ticket', table_name='ticket_messages')
    op.drop_table('ticket_messages')