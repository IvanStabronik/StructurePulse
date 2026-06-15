"""Create strategy versions, analysis snapshots, and candidates.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("parameter_checksum", sa.String(length=64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_versions")),
        sa.UniqueConstraint("version", name=op.f("uq_strategy_versions_version")),
    )
    op.create_table(
        "analysis_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("strategy_version_id", sa.BigInteger(), nullable=False),
        sa.Column("input_signature", sa.String(length=64), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_cutoffs", sa.JSON(), nullable=False),
        sa.Column("market_context", sa.JSON(), nullable=False),
        sa.Column("analyses", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            name=op.f("fk_analysis_snapshots_strategy_version_id_strategy_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["symbol"],
            ["instruments.symbol"],
            name=op.f("fk_analysis_snapshots_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_snapshots")),
        sa.UniqueConstraint(
            "symbol",
            "strategy_version_id",
            "input_signature",
            name="uq_analysis_snapshots_symbol_version_input",
        ),
    )
    op.create_index(
        op.f("ix_analysis_snapshots_strategy_version_id"),
        "analysis_snapshots",
        ["strategy_version_id"],
    )
    op.create_index(
        op.f("ix_analysis_snapshots_symbol"),
        "analysis_snapshots",
        ["symbol"],
    )
    op.create_index(
        "ix_analysis_snapshots_symbol_analyzed",
        "analysis_snapshots",
        ["symbol", "analyzed_at"],
    )
    op.create_table(
        "signal_candidates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("strategy_version_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("strength", sa.String(length=16), nullable=False),
        sa.Column("entry_lower", sa.Numeric(38, 18), nullable=True),
        sa.Column("entry_upper", sa.Numeric(38, 18), nullable=True),
        sa.Column("planned_entry", sa.Numeric(38, 18), nullable=True),
        sa.Column("stop_loss", sa.Numeric(38, 18), nullable=True),
        sa.Column("take_profit_1", sa.Numeric(38, 18), nullable=True),
        sa.Column("take_profit_2", sa.Numeric(38, 18), nullable=True),
        sa.Column("gross_reward_to_risk", sa.Numeric(30, 12), nullable=True),
        sa.Column("net_reward_to_risk", sa.Numeric(30, 12), nullable=True),
        sa.Column("risk_amount", sa.Numeric(30, 12), nullable=True),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=True),
        sa.Column("notional", sa.Numeric(38, 18), nullable=True),
        sa.Column("recommended_leverage", sa.Numeric(20, 8), nullable=True),
        sa.Column("estimated_margin", sa.Numeric(38, 18), nullable=True),
        sa.Column("estimated_entry_fee", sa.Numeric(38, 18), nullable=True),
        sa.Column("estimated_exit_fee", sa.Numeric(38, 18), nullable=True),
        sa.Column("estimated_loss_at_stop", sa.Numeric(38, 18), nullable=True),
        sa.Column("invalidation", sa.String(length=256), nullable=True),
        sa.Column("score_components", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("suppression_reasons", sa.JSON(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["analysis_snapshot_id"],
            ["analysis_snapshots.id"],
            name=op.f("fk_signal_candidates_analysis_snapshot_id_analysis_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            name=op.f("fk_signal_candidates_strategy_version_id_strategy_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["symbol"],
            ["instruments.symbol"],
            name=op.f("fk_signal_candidates_symbol_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_candidates")),
        sa.UniqueConstraint(
            "analysis_snapshot_id",
            "direction",
            name="uq_signal_candidates_snapshot_direction",
        ),
    )
    op.create_index(
        op.f("ix_signal_candidates_analysis_snapshot_id"),
        "signal_candidates",
        ["analysis_snapshot_id"],
    )
    op.create_index(
        op.f("ix_signal_candidates_strategy_version_id"),
        "signal_candidates",
        ["strategy_version_id"],
    )
    op.create_index(
        op.f("ix_signal_candidates_symbol"),
        "signal_candidates",
        ["symbol"],
    )
    op.create_index(
        "ix_signal_candidates_status_created",
        "signal_candidates",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_signal_candidates_symbol_direction",
        "signal_candidates",
        ["symbol", "direction"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_signal_candidates_symbol_direction",
        table_name="signal_candidates",
    )
    op.drop_index(
        "ix_signal_candidates_status_created",
        table_name="signal_candidates",
    )
    op.drop_index(
        op.f("ix_signal_candidates_symbol"),
        table_name="signal_candidates",
    )
    op.drop_index(
        op.f("ix_signal_candidates_strategy_version_id"),
        table_name="signal_candidates",
    )
    op.drop_index(
        op.f("ix_signal_candidates_analysis_snapshot_id"),
        table_name="signal_candidates",
    )
    op.drop_table("signal_candidates")
    op.drop_index(
        "ix_analysis_snapshots_symbol_analyzed",
        table_name="analysis_snapshots",
    )
    op.drop_index(
        op.f("ix_analysis_snapshots_symbol"),
        table_name="analysis_snapshots",
    )
    op.drop_index(
        op.f("ix_analysis_snapshots_strategy_version_id"),
        table_name="analysis_snapshots",
    )
    op.drop_table("analysis_snapshots")
    op.drop_table("strategy_versions")
