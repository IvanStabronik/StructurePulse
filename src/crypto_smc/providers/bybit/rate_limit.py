import asyncio
import random
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from crypto_smc.observability.metrics import BYBIT_RATE_LIMIT_EVENTS


class AdaptiveRateLimiter:
    def __init__(
        self,
        *,
        requests_per_second: float,
        max_concurrency: int,
        retry_base_seconds: float,
    ) -> None:
        self._minimum_interval = 1 / requests_per_second
        self._retry_base_seconds = retry_base_seconds
        self._concurrency = asyncio.Semaphore(max_concurrency)
        self._schedule_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._blocked_until = 0.0

    @asynccontextmanager
    async def request_slot(self) -> AsyncIterator[None]:
        await self._wait_for_schedule()
        async with self._concurrency:
            yield

    async def observe_headers(self, headers: Mapping[str, str]) -> None:
        remaining = self._parse_int(headers.get("X-Bapi-Limit-Status"))
        reset_ms = self._parse_int(headers.get("X-Bapi-Limit-Reset-Timestamp"))
        if remaining is None or reset_ms is None or remaining > 0:
            return

        delay = max(0.0, reset_ms / 1000 - time.time())
        if delay > 0:
            await self.block_for(delay, reason="header_exhausted")

    async def block_for(self, delay_seconds: float, *, reason: str) -> None:
        if delay_seconds <= 0:
            return
        BYBIT_RATE_LIMIT_EVENTS.labels(reason=reason).inc()
        async with self._schedule_lock:
            self._blocked_until = max(
                self._blocked_until,
                time.monotonic() + delay_seconds,
            )

    def retry_delay(
        self,
        *,
        attempt: int,
        headers: Mapping[str, str],
        ip_ban: bool = False,
    ) -> float:
        retry_after = self._parse_float(headers.get("Retry-After"))
        reset_ms = self._parse_int(headers.get("X-Bapi-Limit-Reset-Timestamp"))
        reset_delay = max(0.0, reset_ms / 1000 - time.time()) if reset_ms else 0.0
        exponential = self._retry_base_seconds * (2**attempt)
        jitter = random.uniform(0, exponential * 0.25)
        official_ip_wait = 600.0 if ip_ban else 0.0
        return float(max(retry_after or 0.0, reset_delay, exponential + jitter, official_ip_wait))

    async def _wait_for_schedule(self) -> None:
        async with self._schedule_lock:
            now = time.monotonic()
            scheduled_at = max(now, self._next_request_at, self._blocked_until)
            self._next_request_at = scheduled_at + self._minimum_interval
            delay = scheduled_at - now
        if delay > 0:
            await asyncio.sleep(delay)

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_float(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None
