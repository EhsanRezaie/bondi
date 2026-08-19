"""add profile fields: here_for, pets, workout_frequency, zodiac_sign

Revision ID: 7a2c4e6d8f0b
Revises: 9c1b7d3e5f8a
Create Date: 2026-08-19 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a2c4e6d8f0b'
down_revision: Union[str, Sequence[str], None] = '9c1b7d3e5f8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('user_profiles', sa.Column('here_for', sa.String(length=30), nullable=True))
    op.add_column('user_profiles', sa.Column('pets', sa.String(length=30), nullable=True))
    op.add_column('user_profiles', sa.Column('workout_frequency', sa.String(length=20), nullable=True))
    op.add_column('user_profiles', sa.Column('zodiac_sign', sa.String(length=20), nullable=True))
    # Widen children_status to fit the new longer enum values
    op.alter_column('user_profiles', 'children_status', existing_type=sa.String(length=20), type_=sa.String(length=30), nullable=True)

    op.create_index('idx_profiles_here_for', 'user_profiles', ['here_for'], unique=False)
    op.create_index('idx_profiles_pets', 'user_profiles', ['pets'], unique=False)
    op.create_index('idx_profiles_workout_frequency', 'user_profiles', ['workout_frequency'], unique=False)
    op.create_index('idx_profiles_zodiac_sign', 'user_profiles', ['zodiac_sign'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_profiles_zodiac_sign', table_name='user_profiles')
    op.drop_index('idx_profiles_workout_frequency', table_name='user_profiles')
    op.drop_index('idx_profiles_pets', table_name='user_profiles')
    op.drop_index('idx_profiles_here_for', table_name='user_profiles')
    op.alter_column('user_profiles', 'children_status', existing_type=sa.String(length=30), type_=sa.String(length=20), nullable=True)
    op.drop_column('user_profiles', 'zodiac_sign')
    op.drop_column('user_profiles', 'workout_frequency')
    op.drop_column('user_profiles', 'pets')
    op.drop_column('user_profiles', 'here_for')
