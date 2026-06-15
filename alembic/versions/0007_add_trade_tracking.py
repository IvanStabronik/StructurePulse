"""Add signal fee rate and durable public-trade checkpoint.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column(
            "taker_fee_rate",
            sa.Numeric(20, 12),
            server_default="0.00055",
            nullable=False,
        ),
    )
    op.add_column(
        "virtual_trades",
        sa.Column("last_trade_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "virtual_trades",
        sa.Column("last_trade_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "virtual_trades",
        sa.Column("last_trade_sequence", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("virtual_trades", "last_trade_sequence")
    op.drop_column("virtual_trades", "last_trade_time")
    op.drop_column("virtual_trades", "last_trade_id")
    op.drop_column("signals", "taker_fee_rate")
