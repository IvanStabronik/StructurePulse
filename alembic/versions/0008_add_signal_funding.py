"""Add signal funding inputs and TP1 timestamp.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column(
            "funding_rate",
            sa.Numeric(20, 12),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "signals",
        sa.Column(
            "funding_interval_minutes",
            sa.Integer(),
            server_default="480",
            nullable=False,
        ),
    )
    op.add_column(
        "signals",
        sa.Column("tp1_reached_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("signals", "tp1_reached_at")
    op.drop_column("signals", "funding_interval_minutes")
    op.drop_column("signals", "funding_rate")
