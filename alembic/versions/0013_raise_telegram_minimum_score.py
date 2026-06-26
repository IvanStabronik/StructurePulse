"""Raise Telegram signal visibility threshold."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE telegram_user_settings ALTER COLUMN minimum_score SET DEFAULT 85")
    op.execute("UPDATE telegram_user_settings SET minimum_score = 85 WHERE minimum_score < 85")
    op.execute(
        """
        UPDATE notification_deliveries AS nd
        SET
            status = 'skipped',
            last_error = 'below_score_threshold',
            updated_at = now()
        FROM notification_outbox AS no
        WHERE no.id = nd.outbox_id
          AND nd.status = 'pending'
          AND no.payload ->> 'score' IS NOT NULL
          AND (no.payload ->> 'score')::int < 85
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE telegram_user_settings ALTER COLUMN minimum_score SET DEFAULT 70")
