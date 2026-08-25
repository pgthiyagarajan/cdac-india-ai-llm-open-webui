"""add department, designation, mobile_number columns to user table

Revision ID: d5e6f7a8b9c0
Revises: 461111b60977
Create Date: 2026-08-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = '461111b60977'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    user_cols = {c['name'] for c in inspector.get_columns('user')}

    if 'department' not in user_cols:
        op.add_column('user', sa.Column('department', sa.String(), nullable=True))
    if 'designation' not in user_cols:
        op.add_column('user', sa.Column('designation', sa.String(), nullable=True))
    if 'mobile_number' not in user_cols:
        op.add_column('user', sa.Column('mobile_number', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'mobile_number')
    op.drop_column('user', 'designation')
    op.drop_column('user', 'department')
