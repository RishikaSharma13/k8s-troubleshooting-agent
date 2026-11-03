"""executions.evidence: before/after snapshots, command, dry run and rollout of each execution

Revision ID: a4d7e2c95b13
Revises: f3a8c61d2e94
Create Date: 2026-10-31 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4d7e2c95b13'
down_revision: Union[str, None] = 'f3a8c61d2e94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('executions', sa.Column('evidence', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('executions', 'evidence')
