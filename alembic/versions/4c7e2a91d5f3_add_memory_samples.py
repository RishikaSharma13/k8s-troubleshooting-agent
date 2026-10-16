"""Add memory_samples: measured container memory for forecasting

Revision ID: 4c7e2a91d5f3
Revises: b9f1430d652e
Create Date: 2026-10-16 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4c7e2a91d5f3'
down_revision: Union[str, None] = 'b9f1430d652e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'memory_samples',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('namespace', sa.String(length=255), nullable=False),
        sa.Column('pod', sa.String(length=255), nullable=False),
        sa.Column('container', sa.String(length=255), nullable=False),
        sa.Column('deployment', sa.String(length=255), nullable=True),
        sa.Column('metric_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('window_seconds', sa.Float(), nullable=True),
        sa.Column('memory_bytes', sa.BigInteger(), nullable=False),
        sa.Column('memory_limit_bytes', sa.BigInteger(), nullable=True),
        sa.Column('collected_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('namespace', 'pod', 'container', 'metric_timestamp',
                            name='uq_memory_samples_reading'),
    )
    op.create_index('ix_memory_samples_workload_time', 'memory_samples',
                    ['namespace', 'deployment', 'metric_timestamp'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_memory_samples_workload_time', table_name='memory_samples')
    op.drop_table('memory_samples')
