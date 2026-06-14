"""Create instruments table.

Revision ID: 0001
Revises:
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_coin", sa.String(length=32), nullable=False),
        sa.Column("quote_coin", sa.String(length=16), nullable=False),
        sa.Column("settle_coin", sa.String(length=16), nullable=False),
        sa.Column("contract_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("launch_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tick_size", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("min_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("max_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("quantity_step", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("min_order_quantity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("max_order_quantity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column(
            "max_market_order_quantity",
            sa.Numeric(precision=38, scale=18),
            nullable=False,
        ),
        sa.Column("min_notional_value", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("min_leverage", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("max_leverage", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("leverage_step", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("funding_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.PrimaryKeyConstraint("symbol", name=op.f("pk_instruments")),
    )
    op.create_index(op.f("ix_instruments_base_coin"), "instruments", ["base_coin"])
    op.create_index(op.f("ix_instruments_status"), "instruments", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_instruments_status"), table_name="instruments")
    op.drop_index(op.f("ix_instruments_base_coin"), table_name="instruments")
    op.drop_table("instruments")
