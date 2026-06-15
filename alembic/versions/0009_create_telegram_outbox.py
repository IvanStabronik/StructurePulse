"""Create Telegram settings, outbox, and deliveries.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_user_settings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=2), server_default="ru", nullable=False),
        sa.Column("minimum_score", sa.Integer(), server_default="70", nullable=False),
        sa.Column(
            "schedule_timezone",
            sa.String(length=64),
            server_default="Europe/Warsaw",
            nullable=False,
        ),
        sa.Column("schedule_start", sa.Time(), server_default="07:00", nullable=False),
        sa.Column("schedule_end", sa.Time(), server_default="20:00", nullable=False),
        sa.Column("risk_percent", sa.Numeric(10, 4), server_default="1", nullable=False),
        sa.Column(
            "reference_balance",
            sa.Numeric(20, 4),
            server_default="10000",
            nullable=False,
        ),
        sa.Column("paused", sa.Boolean(), server_default="false", nullable=False),
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
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_telegram_user_settings")),
    )
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_notification_outbox_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_outbox")),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_notification_outbox_idempotency",
        ),
    )
    op.create_index(
        op.f("ix_notification_outbox_signal_id"),
        "notification_outbox",
        ["signal_id"],
    )
    op.create_index(
        "ix_notification_outbox_pending",
        "notification_outbox",
        ["status", "available_at"],
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("outbox_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
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
            ["outbox_id"],
            ["notification_outbox.id"],
            name=op.f("fk_notification_deliveries_outbox_id_notification_outbox"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["telegram_user_settings.user_id"],
            name=op.f("fk_notification_deliveries_user_id_telegram_user_settings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.UniqueConstraint(
            "outbox_id",
            "user_id",
            name="uq_notification_deliveries_outbox_user",
        ),
    )
    op.create_index(
        op.f("ix_notification_deliveries_outbox_id"),
        "notification_deliveries",
        ["outbox_id"],
    )
    op.create_index(
        op.f("ix_notification_deliveries_user_id"),
        "notification_deliveries",
        ["user_id"],
    )
    op.create_index(
        "ix_notification_deliveries_claim",
        "notification_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_claim",
        table_name="notification_deliveries",
    )
    op.drop_index(
        op.f("ix_notification_deliveries_user_id"),
        table_name="notification_deliveries",
    )
    op.drop_index(
        op.f("ix_notification_deliveries_outbox_id"),
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notification_outbox_pending", table_name="notification_outbox")
    op.drop_index(
        op.f("ix_notification_outbox_signal_id"),
        table_name="notification_outbox",
    )
    op.drop_table("notification_outbox")
    op.drop_table("telegram_user_settings")
