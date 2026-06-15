"""Create universe snapshot and member tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "universe_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("source_asset_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_universe_snapshots")),
    )
    op.create_index(
        "uq_universe_snapshots_active",
        "universe_snapshots",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "universe_members",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("asset_symbol", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=128), nullable=False),
        sa.Column("market_cap_rank", sa.Integer(), nullable=False),
        sa.Column("market_cap_usd", sa.Numeric(precision=30, scale=2), nullable=False),
        sa.Column(
            "provider_volume_24h_usd",
            sa.Numeric(precision=30, scale=2),
            nullable=False,
        ),
        sa.Column("instrument_symbol", sa.String(length=32), nullable=True),
        sa.Column(
            "exchange_turnover_24h_usdt",
            sa.Numeric(precision=30, scale=8),
            nullable=True,
        ),
        sa.Column("spread_bps", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("is_selected", sa.Boolean(), nullable=False),
        sa.Column("exclusion_reason", sa.String(length=64), nullable=True),
        sa.Column("decision_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instrument_symbol"],
            ["instruments.symbol"],
            name=op.f("fk_universe_members_instrument_symbol_instruments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["universe_snapshots.id"],
            name=op.f("fk_universe_members_snapshot_id_universe_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_universe_members")),
    )
    op.create_index(
        op.f("ix_universe_members_snapshot_id"),
        "universe_members",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_universe_members_selected",
        "universe_members",
        ["snapshot_id", "is_selected"],
    )
    op.create_index(
        "uq_universe_members_snapshot_provider",
        "universe_members",
        ["snapshot_id", "provider_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_universe_members_snapshot_provider",
        table_name="universe_members",
    )
    op.drop_index("ix_universe_members_selected", table_name="universe_members")
    op.drop_index(
        op.f("ix_universe_members_snapshot_id"),
        table_name="universe_members",
    )
    op.drop_table("universe_members")
    op.drop_index(
        "uq_universe_snapshots_active",
        table_name="universe_snapshots",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("universe_snapshots")
