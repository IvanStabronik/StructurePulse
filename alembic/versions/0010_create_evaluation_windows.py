"""Create live evaluation windows.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_windows",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=96), nullable=False),
        sa.Column("strategy_version_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "minimum_completed_signals",
            sa.Integer(),
            server_default="100",
            nullable=False,
        ),
        sa.Column(
            "minimum_profit_factor",
            sa.Numeric(10, 4),
            server_default="1.3",
            nullable=False,
        ),
        sa.Column(
            "maximum_drawdown_fraction",
            sa.Numeric(10, 6),
            server_default="0.15",
            nullable=False,
        ),
        sa.Column(
            "maximum_symbol_share",
            sa.Numeric(10, 6),
            server_default="0.35",
            nullable=False,
        ),
        sa.Column(
            "reference_balance",
            sa.Numeric(20, 4),
            server_default="10000",
            nullable=False,
        ),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            name=op.f("fk_evaluation_windows_strategy_version_id_strategy_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_windows")),
        sa.UniqueConstraint("name", name=op.f("uq_evaluation_windows_name")),
    )
    op.create_index(
        op.f("ix_evaluation_windows_strategy_version_id"),
        "evaluation_windows",
        ["strategy_version_id"],
    )
    op.create_index(
        "ix_evaluation_windows_strategy_started",
        "evaluation_windows",
        ["strategy_version_id", "started_at"],
    )
    op.create_index(
        "uq_evaluation_windows_active",
        "evaluation_windows",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_evaluation_windows_active", table_name="evaluation_windows")
    op.drop_index(
        "ix_evaluation_windows_strategy_started",
        table_name="evaluation_windows",
    )
    op.drop_index(
        op.f("ix_evaluation_windows_strategy_version_id"),
        table_name="evaluation_windows",
    )
    op.drop_table("evaluation_windows")
