import asyncio
from datetime import timedelta
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.notifications import NotificationRepository
from crypto_smc.telegram.rendering import render_notification

logger = structlog.get_logger(__name__)


class RetryableDeliveryError(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PermanentDeliveryError(RuntimeError):
    pass


class UnknownDeliveryOutcome(RuntimeError):
    pass


class TelegramSender(Protocol):
    async def send(self, user_id: int, text: str) -> int: ...


class NotificationOutboxService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        sender: TelegramSender,
        user_ids: tuple[int, ...],
        poll_seconds: float,
        batch_size: int,
        max_attempts: int,
        retry_base_seconds: float,
        repository: NotificationRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._sender = sender
        self._user_ids = user_ids
        self._poll_seconds = poll_seconds
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._repository = repository or NotificationRepository()

    async def run(self) -> None:
        recovered = await self._repository.recover_stale_sending(self._session_factory)
        if recovered:
            await logger.awarning(
                "telegram_stale_deliveries_marked_unknown",
                count=recovered,
            )
        while True:
            processed = await self.run_once()
            if not processed:
                await asyncio.sleep(self._poll_seconds)

    async def run_once(self) -> bool:
        expanded = await self._repository.materialize_pending(
            self._session_factory,
            user_ids=self._user_ids,
            batch_size=self._batch_size,
        )
        delivery = await self._repository.claim_delivery(
            self._session_factory,
            max_attempts=self._max_attempts,
        )
        if delivery is None:
            return expanded > 0
        message = render_notification(
            delivery.event_type,
            delivery.payload,
            delivery.language,
        )
        try:
            message_id = await self._sender.send(delivery.user_id, message)
        except RetryableDeliveryError as exc:
            delay = max(
                exc.retry_after_seconds,
                self._retry_base_seconds * (2 ** max(0, delivery.attempts - 1)),
            )
            await self._repository.mark_retry(
                self._session_factory,
                delivery_id=delivery.delivery_id,
                error=str(exc),
                retry_after=timedelta(seconds=delay),
            )
        except PermanentDeliveryError as exc:
            await self._repository.mark_failed(
                self._session_factory,
                delivery_id=delivery.delivery_id,
                error=str(exc),
                outcome_unknown=False,
            )
        except UnknownDeliveryOutcome as exc:
            await self._repository.mark_failed(
                self._session_factory,
                delivery_id=delivery.delivery_id,
                error=str(exc),
                outcome_unknown=True,
            )
        else:
            await self._repository.mark_sent(
                self._session_factory,
                delivery_id=delivery.delivery_id,
                message_id=message_id,
            )
        return True
