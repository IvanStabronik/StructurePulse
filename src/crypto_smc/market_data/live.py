import asyncio
from collections.abc import Sequence
from time import monotonic
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.market_data import MarketDataRepository
from crypto_smc.market_data.backfill import STREAM_NAME, MarketDataBackfillService
from crypto_smc.providers.bybit.websocket import (
    ClosedCandleEvent,
    MarketStreamEvent,
    ShardDisconnectedEvent,
    ShardReconnectedEvent,
)
from crypto_smc.services.universe_refresh import UniverseRefreshService

logger = structlog.get_logger(__name__)


class KlineStreamManager(Protocol):
    @property
    def symbols(self) -> tuple[str, ...]: ...

    async def start(self, symbols: Sequence[str]) -> None: ...

    async def wait_until_ready(self) -> None: ...

    async def next_event(self) -> MarketStreamEvent: ...

    async def stop(self) -> None: ...


class LiveMarketDataService:
    def __init__(
        self,
        *,
        stream: KlineStreamManager,
        backfill: MarketDataBackfillService,
        universe_refresh: UniverseRefreshService,
        session_factory: async_sessionmaker[AsyncSession],
        reconciliation_interval_seconds: float,
        universe_refresh_interval_seconds: float = 24 * 60 * 60,
        readiness_event: asyncio.Event | None = None,
        repository: MarketDataRepository | None = None,
    ) -> None:
        self._stream = stream
        self._backfill = backfill
        self._universe_refresh = universe_refresh
        self._session_factory = session_factory
        self._reconciliation_interval_seconds = reconciliation_interval_seconds
        self._universe_refresh_interval_seconds = universe_refresh_interval_seconds
        self._readiness_event = readiness_event
        self._repository = repository or MarketDataRepository()

    async def run(self) -> None:
        self._mark_not_ready()
        await self._universe_refresh.refresh()
        symbols = await self._active_symbols()
        if not symbols:
            raise RuntimeError("No active universe is available for market-data streaming")

        await self._repository.mark_inactive_streams(
            session_factory=self._session_factory,
            active_symbols=symbols,
            stream=STREAM_NAME,
        )
        await self._repository.mark_stream_state(
            session_factory=self._session_factory,
            symbols=symbols,
            stream=STREAM_NAME,
            state="warming",
        )
        await self._stream.start(symbols)
        try:
            await self._stream.wait_until_ready()
            await self._backfill.sync_once()
            self._mark_ready()
            next_reconciliation = monotonic() + self._reconciliation_interval_seconds
            next_universe_refresh = monotonic() + self._universe_refresh_interval_seconds

            while True:
                now = monotonic()
                timeout = max(
                    0.0,
                    min(next_reconciliation, next_universe_refresh) - now,
                )
                try:
                    event = await asyncio.wait_for(
                        self._stream.next_event(),
                        timeout=timeout,
                    )
                except TimeoutError:
                    now = monotonic()
                    if now >= next_universe_refresh:
                        await self._universe_refresh.refresh()
                        await self._replace_symbols_if_changed()
                        next_universe_refresh = now + self._universe_refresh_interval_seconds
                    if now >= next_reconciliation:
                        self._mark_not_ready()
                        await self._backfill.sync_once()
                        self._mark_ready()
                        next_reconciliation = now + self._reconciliation_interval_seconds
                else:
                    await self._handle_event(event)
        finally:
            await self._stream.stop()

    async def _handle_event(self, event: MarketStreamEvent) -> None:
        if isinstance(event, ClosedCandleEvent):
            result = await self._repository.ingest_live_candle(
                session_factory=self._session_factory,
                stream=STREAM_NAME,
                candle=event.candle,
            )
            if result == "gap":
                self._mark_not_ready()
                await logger.awarning(
                    "market_data_live_gap_detected",
                    symbol=event.candle.symbol,
                    open_time=event.candle.open_time,
                )
                await self._backfill.sync_once()
                self._mark_ready()
            return

        if isinstance(event, ShardDisconnectedEvent):
            self._mark_not_ready()
            await self._repository.mark_stream_state(
                session_factory=self._session_factory,
                symbols=event.symbols,
                stream=STREAM_NAME,
                state="degraded",
                error=event.reason,
            )
            return

        if isinstance(event, ShardReconnectedEvent):
            await self._repository.mark_stream_state(
                session_factory=self._session_factory,
                symbols=event.symbols,
                stream=STREAM_NAME,
                state="recovering",
            )
            await self._backfill.sync_once()
            self._mark_ready()

    async def _replace_symbols_if_changed(self) -> None:
        symbols = await self._active_symbols()
        if symbols == self._stream.symbols:
            return
        if not symbols:
            await logger.aerror("market_data_universe_became_empty")
            return

        self._mark_not_ready()
        await self._repository.mark_inactive_streams(
            session_factory=self._session_factory,
            active_symbols=symbols,
            stream=STREAM_NAME,
        )
        await self._repository.mark_stream_state(
            session_factory=self._session_factory,
            symbols=symbols,
            stream=STREAM_NAME,
            state="warming",
        )
        await self._stream.start(symbols)
        await self._stream.wait_until_ready()
        await self._backfill.sync_once()
        self._mark_ready()
        await logger.ainfo(
            "market_data_websocket_symbols_replaced",
            symbol_count=len(symbols),
        )

    async def _active_symbols(self) -> tuple[str, ...]:
        async with self._session_factory() as session:
            symbols = await self._repository.list_active_symbols(session)
        return tuple(sorted(symbols))

    def _mark_ready(self) -> None:
        if self._readiness_event is not None:
            self._readiness_event.set()

    def _mark_not_ready(self) -> None:
        if self._readiness_event is not None:
            self._readiness_event.clear()
