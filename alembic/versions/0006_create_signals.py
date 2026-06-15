"""Create signals, immutable events, and virtual trades.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("suppression_reason", sa.String(length=64), nullable=True),
        sa.Column("entry_lower", sa.Numeric(38, 18), nullable=False),
        sa.Column("entry_upper", sa.Numeric(38, 18), nullable=False),
        sa.Column("planned_entry", sa.Numeric(38, 18), nullable=False),
        sa.Column("stop_loss", sa.Numeric(38, 18), nullable=False),
        sa.Column("take_profit_1", sa.Numeric(38, 18), nullable=False),
        sa.Column("take_profit_2", sa.Numeric(38, 18), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("risk_amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
            ["candidate_id"],
            ["signal_candidates.id"],
            name=op.f("fk_signals_candidate_id_signal_candidates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["symbol"],
            ["instruments.symbol"],
            name=op.f("fk_signals_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signals")),
        sa.UniqueConstraint("candidate_id", name=op.f("uq_signals_candidate_id")),
    )
    op.create_index(op.f("ix_signals_candidate_id"), "signals", ["candidate_id"])
    op.create_index(op.f("ix_signals_symbol"), "signals", ["symbol"])
    op.create_index("ix_signals_status_created", "signals", ["status", "created_at"])
    op.create_index("ix_signals_symbol_created", "signals", ["symbol", "created_at"])
    op.create_index(
        "uq_signals_one_active_per_symbol",
        "signals",
        ["symbol"],
        unique=True,
        postgresql_where=sa.text("status IN ('preparing', 'active', 'entered', 'tp1_reached')"),
    )
    op.create_table(
        "signal_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status_from", sa.String(length=32), nullable=True),
        sa.Column("status_to", sa.String(length=32), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_signal_events_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_events")),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_signal_events_idempotency_key",
        ),
    )
    op.create_index(op.f("ix_signal_events_signal_id"), "signal_events", ["signal_id"])
    op.create_index(
        "ix_signal_events_signal_time",
        "signal_events",
        ["signal_id", "event_time"],
    )
    op.create_table(
        "virtual_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("planned_entry", sa.Numeric(38, 18), nullable=False),
        sa.Column("current_stop", sa.Numeric(38, 18), nullable=False),
        sa.Column("take_profit_1", sa.Numeric(38, 18), nullable=False),
        sa.Column("take_profit_2", sa.Numeric(38, 18), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(38, 18), server_default="0", nullable=False),
        sa.Column("fees", sa.Numeric(38, 18), server_default="0", nullable=False),
        sa.Column(
            "estimated_funding",
            sa.Numeric(38, 18),
            server_default="0",
            nullable=False,
        ),
        sa.Column("r_multiple", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("ambiguous", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
            name=op.f("fk_virtual_trades_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_virtual_trades")),
        sa.UniqueConstraint("signal_id", name=op.f("uq_virtual_trades_signal_id")),
    )
    op.create_index(op.f("ix_virtual_trades_signal_id"), "virtual_trades", ["signal_id"])
    op.create_index(
        "ix_virtual_trades_status_updated",
        "virtual_trades",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_virtual_trades_status_updated", table_name="virtual_trades")
    op.drop_index(op.f("ix_virtual_trades_signal_id"), table_name="virtual_trades")
    op.drop_table("virtual_trades")
    op.drop_index("ix_signal_events_signal_time", table_name="signal_events")
    op.drop_index(op.f("ix_signal_events_signal_id"), table_name="signal_events")
    op.drop_table("signal_events")
    op.drop_index("uq_signals_one_active_per_symbol", table_name="signals")
    op.drop_index("ix_signals_symbol_created", table_name="signals")
    op.drop_index("ix_signals_status_created", table_name="signals")
    op.drop_index(op.f("ix_signals_symbol"), table_name="signals")
    op.drop_index(op.f("ix_signals_candidate_id"), table_name="signals")
    op.drop_table("signals")
