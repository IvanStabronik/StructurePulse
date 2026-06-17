"""Create live execution audit table.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_executions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("order_budget_usdt", sa.Numeric(30, 12), nullable=False),
        sa.Column("entry_order_id", sa.String(length=128), nullable=True),
        sa.Column("tp1_order_id", sa.String(length=128), nullable=True),
        sa.Column("close_order_id", sa.String(length=128), nullable=True),
        sa.Column("entry_order_link_id", sa.String(length=128), nullable=True),
        sa.Column("tp1_order_link_id", sa.String(length=128), nullable=True),
        sa.Column("close_order_link_id", sa.String(length=128), nullable=True),
        sa.Column("entry_qty", sa.Numeric(38, 18), nullable=False),
        sa.Column("remaining_qty", sa.Numeric(38, 18), nullable=False),
        sa.Column("entry_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("current_stop", sa.Numeric(38, 18), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("entry_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tp1_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_live_executions_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_live_executions")),
        sa.UniqueConstraint("signal_id", name=op.f("uq_live_executions_signal_id")),
    )
    op.create_index(op.f("ix_live_executions_signal_id"), "live_executions", ["signal_id"])
    op.create_index(
        "ix_live_executions_status_created",
        "live_executions",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_live_executions_symbol_created",
        "live_executions",
        ["symbol", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_live_executions_symbol_created", table_name="live_executions")
    op.drop_index("ix_live_executions_status_created", table_name="live_executions")
    op.drop_index(op.f("ix_live_executions_signal_id"), table_name="live_executions")
    op.drop_table("live_executions")
