"""Create deterministic candle aggregation storage and work queue.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candles_agg",
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("high_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("low_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("close_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("volume", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("turnover", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("source_candle_count", sa.Integer(), nullable=False),
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
            ["symbol"],
            ["instruments.symbol"],
            name=op.f("fk_candles_agg_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "symbol",
            "timeframe",
            "open_time",
            name=op.f("pk_candles_agg"),
        ),
    )
    op.create_index(
        "ix_candles_agg_timeframe_open_time",
        "candles_agg",
        ["timeframe", "open_time"],
    )

    op.create_table(
        "aggregation_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "available_at",
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
            ["symbol"],
            ["instruments.symbol"],
            name=op.f("fk_aggregation_jobs_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_aggregation_jobs")),
        sa.UniqueConstraint(
            "symbol",
            "timeframe",
            "open_time",
            name="uq_aggregation_jobs_interval",
        ),
    )
    op.create_index(
        op.f("ix_aggregation_jobs_symbol"),
        "aggregation_jobs",
        ["symbol"],
    )
    op.create_index(
        "ix_aggregation_jobs_claim",
        "aggregation_jobs",
        ["state", "priority", "available_at"],
    )

    op.create_table(
        "aggregation_cursors",
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("last_scanned_open_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["symbol"],
            ["instruments.symbol"],
            name=op.f("fk_aggregation_cursors_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("symbol", name=op.f("pk_aggregation_cursors")),
    )


def downgrade() -> None:
    op.drop_table("aggregation_cursors")
    op.drop_index("ix_aggregation_jobs_claim", table_name="aggregation_jobs")
    op.drop_index(op.f("ix_aggregation_jobs_symbol"), table_name="aggregation_jobs")
    op.drop_table("aggregation_jobs")
    op.drop_index("ix_candles_agg_timeframe_open_time", table_name="candles_agg")
    op.drop_table("candles_agg")
