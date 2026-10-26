"""Add incidents.approved_action: approval is bound to one exact action

Revision ID: e7d24b9c1a58
Revises: c3f19a6e2b71
Create Date: 2026-10-22 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7d24b9c1a58'
down_revision: Union[str, None] = 'c3f19a6e2b71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('incidents', sa.Column('approved_action', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('incidents', 'approved_action')
