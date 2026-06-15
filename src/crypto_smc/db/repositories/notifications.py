from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import (
    NotificationDeliveryRecord,
    NotificationOutboxRecord,
    TelegramUserSettingsRecord,
)


@dataclass(frozen=True, slots=True)
class TelegramUserSettings:
    user_id: int
    language: str
    minimum_score: int
    schedule_timezone: str
    schedule_start: time
    schedule_end: time
    risk_percent: Decimal
    reference_balance: Decimal
    paused: bool


@dataclass(frozen=True, slots=True)
class PendingDelivery:
    delivery_id: int
    outbox_id: int
    user_id: int
    event_type: str
    payload: dict[str, object]
    language: str
    attempts: int


@dataclass(frozen=True, slots=True)
class OutboxSummary:
    pending: int
    sent: int
    failed: int
    delivery_unknown: int


class NotificationRepository:
    async def ensure_users(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        user_ids: tuple[int, ...],
        default_language: str,
        schedule_timezone: str,
        schedule_start: time,
        schedule_end: time,
    ) -> None:
        if not user_ids:
            return
        async with session_factory() as session, session.begin():
            for user_id in user_ids:
                statement = insert(TelegramUserSettingsRecord).values(
                    user_id=user_id,
                    language=default_language,
                    schedule_timezone=schedule_timezone,
                    schedule_start=schedule_start,
                    schedule_end=schedule_end,
                )
                await session.execute(
                    statement.on_conflict_do_nothing(
                        index_elements=[TelegramUserSettingsRecord.user_id]
                    )
                )

    async def get_user(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_id: int,
    ) -> TelegramUserSettings | None:
        async with session_factory() as session:
            record = await session.get(TelegramUserSettingsRecord, user_id)
        return _settings_view(record) if record is not None else None

    async def update_user(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_id: int,
        **values: object,
    ) -> TelegramUserSettings:
        allowed = {
            "language",
            "minimum_score",
            "schedule_timezone",
            "schedule_start",
            "schedule_end",
            "risk_percent",
            "reference_balance",
            "paused",
        }
        if not values or not set(values).issubset(allowed):
            raise ValueError("Invalid Telegram user settings update")
        async with session_factory() as session, session.begin():
            record = await session.scalar(
                select(TelegramUserSettingsRecord)
                .where(TelegramUserSettingsRecord.user_id == user_id)
                .with_for_update()
            )
            if record is None:
                raise ValueError(f"Telegram user {user_id} is not configured")
            for name, value in values.items():
                setattr(record, name, value)
            record.updated_at = datetime.now(UTC)
        return _settings_view(record)

    async def materialize_pending(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        user_ids: tuple[int, ...],
        batch_size: int,
        now: datetime | None = None,
    ) -> int:
        if not user_ids:
            return 0
        current_time = now or datetime.now(UTC)
        async with session_factory() as session, session.begin():
            outbox_records = (
                await session.scalars(
                    select(NotificationOutboxRecord)
                    .where(
                        NotificationOutboxRecord.status == "pending",
                        NotificationOutboxRecord.available_at <= current_time,
                    )
                    .order_by(NotificationOutboxRecord.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            users = (
                await session.scalars(
                    select(TelegramUserSettingsRecord).where(
                        TelegramUserSettingsRecord.user_id.in_(user_ids)
                    )
                )
            ).all()
            for outbox in outbox_records:
                for user in users:
                    status, reason = delivery_policy(outbox, user, now=current_time)
                    statement = insert(NotificationDeliveryRecord).values(
                        outbox_id=outbox.id,
                        user_id=user.user_id,
                        status=status,
                        next_attempt_at=current_time,
                        last_error=reason,
                    )
                    await session.execute(
                        statement.on_conflict_do_nothing(
                            constraint="uq_notification_deliveries_outbox_user"
                        )
                    )
                outbox.status = "expanded"
                outbox.processed_at = current_time
        return len(outbox_records)

    async def recover_stale_sending(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> int:
        cutoff = datetime.now(UTC) - stale_after
        async with session_factory() as session, session.begin():
            records = (
                await session.scalars(
                    select(NotificationDeliveryRecord)
                    .where(
                        NotificationDeliveryRecord.status == "sending",
                        NotificationDeliveryRecord.updated_at < cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for record in records:
                record.status = "delivery_unknown"
                record.last_error = "stale_sending_state_not_retried"
        return len(records)

    async def claim_delivery(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int,
    ) -> PendingDelivery | None:
        now = datetime.now(UTC)
        async with session_factory() as session, session.begin():
            paused_records = (
                await session.scalars(
                    select(NotificationDeliveryRecord)
                    .join(
                        TelegramUserSettingsRecord,
                        TelegramUserSettingsRecord.user_id == NotificationDeliveryRecord.user_id,
                    )
                    .where(
                        NotificationDeliveryRecord.status == "pending",
                        TelegramUserSettingsRecord.paused.is_(True),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for record in paused_records:
                record.status = "skipped"
                record.last_error = "notifications_paused"
                record.updated_at = now

            exhausted_records = (
                await session.scalars(
                    select(NotificationDeliveryRecord)
                    .where(
                        NotificationDeliveryRecord.status == "pending",
                        NotificationDeliveryRecord.attempts >= max_attempts,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for record in exhausted_records:
                record.status = "failed"
                record.last_error = "max_attempts_exhausted"
                record.updated_at = now

            row = (
                await session.execute(
                    select(
                        NotificationDeliveryRecord,
                        NotificationOutboxRecord,
                        TelegramUserSettingsRecord,
                    )
                    .join(
                        NotificationOutboxRecord,
                        NotificationOutboxRecord.id == NotificationDeliveryRecord.outbox_id,
                    )
                    .join(
                        TelegramUserSettingsRecord,
                        TelegramUserSettingsRecord.user_id == NotificationDeliveryRecord.user_id,
                    )
                    .where(
                        NotificationDeliveryRecord.status == "pending",
                        NotificationDeliveryRecord.next_attempt_at <= now,
                        NotificationDeliveryRecord.attempts < max_attempts,
                    )
                    .order_by(NotificationDeliveryRecord.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).first()
            if row is None:
                return None
            delivery, outbox, user = row
            delivery.status = "sending"
            delivery.attempts += 1
            delivery.updated_at = now
            return PendingDelivery(
                delivery_id=delivery.id,
                outbox_id=outbox.id,
                user_id=delivery.user_id,
                event_type=outbox.event_type,
                payload=outbox.payload,
                language=user.language,
                attempts=delivery.attempts,
            )

    async def mark_sent(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        delivery_id: int,
        message_id: int,
    ) -> None:
        await self._finish_delivery(
            session_factory,
            delivery_id=delivery_id,
            status="sent",
            message_id=message_id,
        )

    async def mark_retry(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        delivery_id: int,
        error: str,
        retry_after: timedelta,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await session.scalar(
                select(NotificationDeliveryRecord)
                .where(NotificationDeliveryRecord.id == delivery_id)
                .with_for_update()
            )
            if record is None or record.status != "sending":
                return
            record.status = "pending"
            record.last_error = error[:2000]
            record.next_attempt_at = datetime.now(UTC) + retry_after

    async def mark_failed(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        delivery_id: int,
        error: str,
        outcome_unknown: bool,
    ) -> None:
        await self._finish_delivery(
            session_factory,
            delivery_id=delivery_id,
            status="delivery_unknown" if outcome_unknown else "failed",
            error=error,
        )

    async def summary(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> OutboxSummary:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        NotificationDeliveryRecord.status,
                        func.count(),
                    ).group_by(NotificationDeliveryRecord.status)
                )
            ).all()
        counts = {status: count for status, count in rows}
        return OutboxSummary(
            pending=counts.get("pending", 0) + counts.get("sending", 0),
            sent=counts.get("sent", 0),
            failed=counts.get("failed", 0),
            delivery_unknown=counts.get("delivery_unknown", 0),
        )

    async def _finish_delivery(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        delivery_id: int,
        status: str,
        message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        async with session_factory() as session, session.begin():
            record = await session.scalar(
                select(NotificationDeliveryRecord)
                .where(NotificationDeliveryRecord.id == delivery_id)
                .with_for_update()
            )
            if record is None or record.status != "sending":
                return
            record.status = status
            record.telegram_message_id = message_id
            record.last_error = error[:2000] if error else None
            record.sent_at = datetime.now(UTC) if status == "sent" else None


def delivery_policy(
    outbox: NotificationOutboxRecord,
    user: TelegramUserSettingsRecord,
    *,
    now: datetime,
) -> tuple[str, str | None]:
    if user.paused:
        return "skipped", "notifications_paused"
    if outbox.event_type != "new_signal":
        return "pending", None
    score = int(outbox.payload.get("score", 0))
    if score < user.minimum_score:
        return "skipped", "below_score_threshold"
    if not notification_window_is_open(
        now,
        timezone=user.schedule_timezone,
        start=user.schedule_start,
        end=user.schedule_end,
    ):
        return "skipped", "outside_notification_schedule"
    return "pending", None


def notification_window_is_open(
    now: datetime,
    *,
    timezone: str,
    start: time,
    end: time,
) -> bool:
    try:
        local_time = now.astimezone(ZoneInfo(timezone)).time().replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        return False
    if start == end:
        return True
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def _settings_view(record: TelegramUserSettingsRecord) -> TelegramUserSettings:
    return TelegramUserSettings(
        user_id=record.user_id,
        language=record.language,
        minimum_score=record.minimum_score,
        schedule_timezone=record.schedule_timezone,
        schedule_start=record.schedule_start,
        schedule_end=record.schedule_end,
        risk_percent=record.risk_percent,
        reference_balance=record.reference_balance,
        paused=record.paused,
    )
