import asyncio
from dataclasses import dataclass
from time import monotonic

import structlog

from crypto_smc.observability.metrics import (
    EVENT_LOOP_LAG_SECONDS,
    EVENT_LOOP_LAG_WARNINGS,
    WORKER_QUIESCING,
    WORKER_READY,
)

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class WorkerRuntimeState:
    market_data_ready: asyncio.Event
    quiescing: bool = False

    def begin_quiescence(self) -> None:
        self.quiescing = True
        self.market_data_ready.clear()
        WORKER_QUIESCING.set(1)
        WORKER_READY.set(0)

    def update_ready_metric(self) -> None:
        WORKER_READY.set(int(self.market_data_ready.is_set() and not self.quiescing))


class EventLoopMonitor:
    def __init__(
        self,
        *,
        interval_seconds: float,
        warning_seconds: float,
        runtime: WorkerRuntimeState | None = None,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._warning_seconds = warning_seconds
        self._runtime = runtime
        self._warning_active = False

    async def run(self) -> None:
        expected = monotonic() + self._interval_seconds
        while True:
            await asyncio.sleep(self._interval_seconds)
            now = monotonic()
            lag = max(0.0, now - expected)
            EVENT_LOOP_LAG_SECONDS.set(lag)
            if self._runtime is not None:
                self._runtime.update_ready_metric()
            if lag >= self._warning_seconds and not self._warning_active:
                self._warning_active = True
                EVENT_LOOP_LAG_WARNINGS.inc()
                await logger.awarning(
                    "event_loop_lag_threshold_exceeded",
                    lag_seconds=round(lag, 6),
                    threshold_seconds=self._warning_seconds,
                )
            elif lag < self._warning_seconds and self._warning_active:
                self._warning_active = False
                await logger.ainfo("event_loop_lag_recovered", lag_seconds=round(lag, 6))
            expected = now + self._interval_seconds
