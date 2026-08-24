"""add photo phash column for duplicate-upload detection

Revision ID: 5d6d70f2e5b0
Revises: 7a2c4e6d8f0b
Create Date: 2026-08-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d6d70f2e5b0'
down_revision: Union[str, Sequence[str], None] = '7a2c4e6d8f0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('photos', sa.Column('phash', sa.String(length=64), nullable=True))
    op.create_index('idx_photos_phash', 'photos', ['user_id', 'phash'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_photos_phash', table_name='photos')
    op.drop_column('photos', 'phash')
