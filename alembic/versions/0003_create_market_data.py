"""Create canonical 1m market-data tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candles_1m",
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("high_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("low_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("close_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("volume", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("turnover", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="rest", nullable=False),
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
            name=op.f("fk_candles_1m_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("symbol", "open_time", name=op.f("pk_candles_1m")),
        postgresql_partition_by="RANGE (open_time)",
    )
    op.create_index("ix_candles_1m_open_time", "candles_1m", ["open_time"])

    op.create_table(
        "data_checkpoints",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("stream", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "last_confirmed_open_time",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["symbol"],
            ["instruments.symbol"],
            name=op.f("fk_data_checkpoints_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_checkpoints")),
        sa.UniqueConstraint(
            "symbol",
            "stream",
            name="uq_data_checkpoints_symbol_stream",
        ),
    )
    op.create_index(
        op.f("ix_data_checkpoints_symbol"),
        "data_checkpoints",
        ["symbol"],
    )
    op.create_index("ix_data_checkpoints_state", "data_checkpoints", ["state"])

    op.create_table(
        "data_gaps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("stream", sa.String(length=32), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["symbol"],
            ["instruments.symbol"],
            name=op.f("fk_data_gaps_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_gaps")),
    )
    op.create_index(op.f("ix_data_gaps_symbol"), "data_gaps", ["symbol"])
    op.create_index(
        "ix_data_gaps_symbol_status",
        "data_gaps",
        ["symbol", "status"],
    )
    op.create_index("ix_data_gaps_detected_at", "data_gaps", ["detected_at"])


def downgrade() -> None:
    op.drop_index("ix_data_gaps_detected_at", table_name="data_gaps")
    op.drop_index("ix_data_gaps_symbol_status", table_name="data_gaps")
    op.drop_index(op.f("ix_data_gaps_symbol"), table_name="data_gaps")
    op.drop_table("data_gaps")
    op.drop_index("ix_data_checkpoints_state", table_name="data_checkpoints")
    op.drop_index(
        op.f("ix_data_checkpoints_symbol"),
        table_name="data_checkpoints",
    )
    op.drop_table("data_checkpoints")
    op.drop_index("ix_candles_1m_open_time", table_name="candles_1m")
    op.drop_table("candles_1m")
