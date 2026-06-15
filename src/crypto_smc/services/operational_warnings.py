import asyncio
from datetime import UTC, datetime
from time import monotonic

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.notifications import NotificationRepository
from crypto_smc.observability.runtime import WorkerRuntimeState

logger = structlog.get_logger(__name__)


class OperationalWarningService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        runtime: WorkerRuntimeState,
        interval_seconds: float,
        warning_delay_seconds: float,
        cooldown_seconds: int,
        repository: NotificationRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._runtime = runtime
        self._interval_seconds = interval_seconds
        self._warning_delay_seconds = warning_delay_seconds
        self._cooldown_seconds = cooldown_seconds
        self._repository = repository or NotificationRepository()
        self._unready_since: float | None = None
        self._warning_emitted = False

    async def run(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception:
                await logger.aexception("operational_warning_check_failed")
            await asyncio.sleep(self._interval_seconds)

    async def check_once(self, *, now: datetime | None = None) -> bool:
        if self._runtime.quiescing:
            return False
        if self._runtime.market_data_ready.is_set():
            return await self._handle_recovery(now=now)

        if self._unready_since is None:
            self._unready_since = monotonic()
            return False
        if monotonic() - self._unready_since < self._warning_delay_seconds:
            return False

        current_time = now or datetime.now(UTC)
        bucket = int(current_time.timestamp()) // self._cooldown_seconds
        created = await self._repository.enqueue_operational_event(
            self._session_factory,
            idempotency_key=f"service:market-data-unready:{bucket}",
            event_type="service_warning",
            payload={
                "service": "market_data",
                "status": "degraded",
                "reason": "market_data_not_ready",
                "event_time": current_time.isoformat(),
            },
            available_at=current_time,
        )
        self._warning_emitted = self._warning_emitted or created
        return created

    async def _handle_recovery(self, *, now: datetime | None) -> bool:
        self._unready_since = None
        if not self._warning_emitted:
            return False
        current_time = now or datetime.now(UTC)
        self._warning_emitted = False
        return await self._repository.enqueue_operational_event(
            self._session_factory,
            idempotency_key=f"service:market-data-recovered:{int(current_time.timestamp())}",
            event_type="service_recovered",
            payload={
                "service": "market_data",
                "status": "ready",
                "reason": "market_data_recovered",
                "event_time": current_time.isoformat(),
            },
            available_at=current_time,
        )
