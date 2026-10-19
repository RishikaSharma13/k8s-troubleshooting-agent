"""Add container_terminations: observed terminations (ground truth for prediction)

Revision ID: 8e1b5d27c9a4
Revises: 4c7e2a91d5f3
Create Date: 2026-10-19 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e1b5d27c9a4'
down_revision: Union[str, None] = '4c7e2a91d5f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'container_terminations',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('namespace', sa.String(length=255), nullable=False),
        sa.Column('pod', sa.String(length=255), nullable=False),
        sa.Column('container', sa.String(length=255), nullable=False),
        sa.Column('deployment', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.String(length=100), nullable=True),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('restart_count', sa.Integer(), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('namespace', 'pod', 'container', 'finished_at',
                            name='uq_container_terminations_event'),
    )
    op.create_index('ix_container_terminations_workload_time', 'container_terminations',
                    ['namespace', 'deployment', 'finished_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_container_terminations_workload_time', table_name='container_terminations')
    op.drop_table('container_terminations')
