"""incidents.outcome_detail: the evidence behind each verification outcome

Revision ID: b6e1f04a8c27
Revises: a4d7e2c95b13
Create Date: 2026-11-04 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6e1f04a8c27'
down_revision: Union[str, None] = 'a4d7e2c95b13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('incidents', sa.Column('outcome_detail', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('incidents', 'outcome_detail')
