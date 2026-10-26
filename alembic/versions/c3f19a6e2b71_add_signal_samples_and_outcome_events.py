"""Add signal_samples and outcome_events (storage and job-deadline predictions)

Revision ID: c3f19a6e2b71
Revises: 8e1b5d27c9a4
Create Date: 2026-10-22 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f19a6e2b71'
down_revision: Union[str, None] = '8e1b5d27c9a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'signal_samples',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('namespace', sa.String(length=255), nullable=False),
        sa.Column('workload_kind', sa.String(length=50), nullable=False),
        sa.Column('workload', sa.String(length=255), nullable=False),
        sa.Column('object_name', sa.String(length=255), nullable=False),
        sa.Column('signal', sa.String(length=100), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('limit_value', sa.Float(), nullable=True),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('collected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('namespace', 'object_name', 'signal', 'observed_at', name='uq_signal_samples_reading'),
    )
    op.create_index('ix_signal_samples_workload_time', 'signal_samples',
                    ['namespace', 'signal', 'workload', 'observed_at'], unique=False)
    op.create_table(
        'outcome_events',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('namespace', sa.String(length=255), nullable=False),
        sa.Column('workload_kind', sa.String(length=50), nullable=False),
        sa.Column('workload', sa.String(length=255), nullable=False),
        sa.Column('object_name', sa.String(length=255), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('message', sa.String(length=1000), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('namespace', 'object_name', 'event_type', name='uq_outcome_events_event'),
    )
    op.create_index('ix_outcome_events_workload_time', 'outcome_events',
                    ['namespace', 'workload', 'occurred_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_outcome_events_workload_time', table_name='outcome_events')
    op.drop_table('outcome_events')
    op.drop_index('ix_signal_samples_workload_time', table_name='signal_samples')
    op.drop_table('signal_samples')
