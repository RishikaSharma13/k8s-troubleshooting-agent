"""Forecast-raised incidents: source, predicted failure time, container life, outcome

Revision ID: f3a8c61d2e94
Revises: e7d24b9c1a58
Create Date: 2026-10-30 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a8c61d2e94'
down_revision: Union[str, None] = 'e7d24b9c1a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('incidents', sa.Column('source', sa.String(length=50), nullable=True))
    op.add_column('incidents', sa.Column('predicted_failure_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('incidents', sa.Column('life_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('incidents', sa.Column('outcome', sa.String(length=50), nullable=True))
    # At most one forecast incident per container life (the original incidents are unaffected)
    op.create_index('uq_incidents_forecast_life', 'incidents',
                    ['namespace', 'deployment', 'pod_name', 'life_started_at'], unique=True,
                    postgresql_where=sa.text("source = 'forecast'"))


def downgrade() -> None:
    op.drop_index('uq_incidents_forecast_life', table_name='incidents', postgresql_where=sa.text("source = 'forecast'"))
    op.drop_column('incidents', 'outcome')
    op.drop_column('incidents', 'life_started_at')
    op.drop_column('incidents', 'predicted_failure_at')
    op.drop_column('incidents', 'source')
