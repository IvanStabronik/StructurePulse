import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.market_data import (
    MarketDataRepository,
    MarketDataTarget,
)
from crypto_smc.observability.metrics import (
    MARKET_DATA_SYNC_RESULTS,
    MARKET_DATA_UNRESOLVED_GAPS,
)
from crypto_smc.providers.models import Candle1m
from crypto_smc.providers.protocols import KlineProvider

logger = structlog.get_logger(__name__)

ONE_MINUTE = timedelta(minutes=1)
STREAM_NAME = "kline_1m"


class IncompleteKlineRangeError(RuntimeError):
    """Raised when Bybit does not return every expected closed minute."""


class MarketDataBackfillService:
    def __init__(
        self,
        *,
        provider: KlineProvider,
        session_factory: async_sessionmaker[AsyncSession],
        initial_history_minutes: int,
        batch_candles: int,
        max_parallel_symbols: int,
        repository: MarketDataRepository | None = None,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._initial_history_minutes = initial_history_minutes
        self._batch_candles = batch_candles
        self._parallelism = asyncio.Semaphore(max_parallel_symbols)
        self._repository = repository or MarketDataRepository()

    async def sync_once(self) -> dict[str, int]:
        server_time = datetime.fromtimestamp(
            await self._provider.server_time_ms() / 1000,
            tz=UTC,
        )
        last_closed_open_time = self.last_closed_minute(server_time)
        async with self._session_factory() as session:
            targets = await self._repository.list_active_targets(session)
            checkpoints = await self._repository.get_checkpoints(
                session,
                stream=STREAM_NAME,
            )

        prioritized = sorted(
            targets,
            key=lambda target: (
                0 if target.symbol == "BTCUSDT" else 1 if target.symbol == "ETHUSDT" else 2,
                target.market_cap_rank,
            ),
        )
        results = await asyncio.gather(
            *[
                self._sync_target(
                    target=target,
                    checkpoint_time=(
                        checkpoints[target.symbol].last_confirmed_open_time
                        if target.symbol in checkpoints
                        else None
                    ),
                    last_closed_open_time=last_closed_open_time,
                )
                for target in prioritized
            ]
        )
        summary = {
            "ready": sum(result == "ready" for result in results),
            "recovered": sum(result == "recovered" for result in results),
            "failed": sum(result == "failed" for result in results),
        }
        for result, count in summary.items():
            MARKET_DATA_SYNC_RESULTS.labels(result=result).inc(count)
        async with self._session_factory() as session:
            status = await self._repository.status_summary(session)
        MARKET_DATA_UNRESOLVED_GAPS.set(cast(int, status["unresolved_gaps"]))
        await logger.ainfo("market_data_sync_completed", **summary)
        return summary

    async def _sync_target(
        self,
        *,
        target: MarketDataTarget,
        checkpoint_time: datetime | None,
        last_closed_open_time: datetime,
    ) -> str:
        start_time = self._start_time(
            target=target,
            checkpoint_time=checkpoint_time,
            last_closed_open_time=last_closed_open_time,
        )
        if start_time > last_closed_open_time:
            if checkpoint_time is not None:
                await self._repository.complete_gap(
                    session_factory=self._session_factory,
                    gap_id=None,
                    symbol=target.symbol,
                    stream=STREAM_NAME,
                    last_confirmed_open_time=checkpoint_time,
                )
            return "ready"

        async with self._parallelism:
            gap_id = None
            if self._requires_gap(
                checkpoint_time=checkpoint_time,
                start_time=start_time,
                end_time=last_closed_open_time,
            ):
                gap_id = await self._repository.start_gap(
                    session_factory=self._session_factory,
                    symbol=target.symbol,
                    stream=STREAM_NAME,
                    start_time=start_time,
                    end_time=last_closed_open_time,
                )
            cursor = start_time
            try:
                while cursor <= last_closed_open_time:
                    batch_end = min(
                        cursor + ONE_MINUTE * (self._batch_candles - 1),
                        last_closed_open_time,
                    )
                    candles = await self._provider.get_closed_1m_klines(
                        symbol=target.symbol,
                        start_time=cursor,
                        end_time=batch_end,
                        limit=self._batch_candles,
                    )
                    self._validate_contiguous(
                        candles,
                        start_time=cursor,
                        end_time=batch_end,
                    )
                    await self._repository.save_batch(
                        session_factory=self._session_factory,
                        symbol=target.symbol,
                        stream=STREAM_NAME,
                        candles=candles,
                    )
                    cursor = batch_end + ONE_MINUTE

                await self._repository.complete_gap(
                    session_factory=self._session_factory,
                    gap_id=gap_id,
                    symbol=target.symbol,
                    stream=STREAM_NAME,
                    last_confirmed_open_time=last_closed_open_time,
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                await self._repository.fail_gap(
                    session_factory=self._session_factory,
                    gap_id=gap_id,
                    symbol=target.symbol,
                    stream=STREAM_NAME,
                    error=error,
                )
                await logger.aexception(
                    "market_data_backfill_failed",
                    symbol=target.symbol,
                    start_time=start_time,
                    end_time=last_closed_open_time,
                )
                return "failed"
        return "recovered"

    def _start_time(
        self,
        *,
        target: MarketDataTarget,
        checkpoint_time: datetime | None,
        last_closed_open_time: datetime,
    ) -> datetime:
        if checkpoint_time is not None:
            return checkpoint_time + ONE_MINUTE

        history_start = last_closed_open_time - ONE_MINUTE * (self._initial_history_minutes - 1)
        launch_minute = target.launch_time.astimezone(UTC).replace(second=0, microsecond=0)
        return max(history_start, launch_minute)

    @staticmethod
    def last_closed_minute(server_time: datetime) -> datetime:
        current_open = server_time.astimezone(UTC).replace(second=0, microsecond=0)
        return current_open - ONE_MINUTE

    @staticmethod
    def _requires_gap(
        *,
        checkpoint_time: datetime | None,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        return checkpoint_time is None or start_time < end_time

    @staticmethod
    def _validate_contiguous(
        candles: list[Candle1m],
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        expected_count = int((end_time - start_time) / ONE_MINUTE) + 1
        if len(candles) != expected_count:
            raise IncompleteKlineRangeError(
                f"expected {expected_count} candles, received {len(candles)}"
            )
        for index, candle in enumerate(candles):
            expected_time = start_time + ONE_MINUTE * index
            if candle.open_time != expected_time:
                raise IncompleteKlineRangeError(
                    f"expected {expected_time.isoformat()}, received {candle.open_time.isoformat()}"
                )
