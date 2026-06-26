"""Add real PnL fields to live executions."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_executions",
        sa.Column("real_pnl", sa.Numeric(precision=38, scale=18), nullable=True),
    )
    op.add_column(
        "live_executions",
        sa.Column("real_entry_price", sa.Numeric(precision=38, scale=18), nullable=True),
    )
    op.add_column(
        "live_executions",
        sa.Column("real_exit_price", sa.Numeric(precision=38, scale=18), nullable=True),
    )
    op.execute(
        """
        UPDATE live_executions AS le
        SET
            real_pnl = NULLIF(n.payload ->> 'real_pnl_usdt', '')::numeric,
            real_entry_price = NULLIF(n.payload ->> 'real_entry_price', '')::numeric,
            real_exit_price = NULLIF(n.payload ->> 'real_exit_price', '')::numeric
        FROM notification_outbox AS n
        WHERE n.event_type = 'live_position_closed'
          AND n.signal_id = le.signal_id
          AND le.real_pnl IS NULL
          AND n.payload ->> 'real_pnl_usdt' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("live_executions", "real_exit_price")
    op.drop_column("live_executions", "real_entry_price")
    op.drop_column("live_executions", "real_pnl")
