from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_smc.market_data.live import LiveMarketDataService
from crypto_smc.providers.bybit.websocket import (
    ClosedCandleEvent,
    ShardDisconnectedEvent,
    ShardReconnectedEvent,
)
from crypto_smc.providers.models import Candle1m


class FakeRepository:
    def __init__(self, ingest_result: str = "ready") -> None:
        self.ingest_result = ingest_result
        self.states: list[tuple[tuple[str, ...], str, str | None]] = []

    async def ingest_live_candle(self, **_: object) -> str:
        return self.ingest_result

    async def mark_stream_state(
        self,
        *,
        symbols: tuple[str, ...],
        state: str,
        error: str | None = None,
        **_: object,
    ) -> None:
        self.states.append((symbols, state, error))

    async def mark_inactive_streams(self, **_: object) -> None:
        return None


class FakeBackfill:
    def __init__(self) -> None:
        self.calls = 0

    async def sync_once(self) -> dict[str, int]:
        self.calls += 1
        return {"ready": 1, "recovered": 0, "failed": 0}


def candle() -> Candle1m:
    return Candle1m(
        symbol="BTCUSDT",
        open_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
        open_price=Decimal("100"),
        high_price=Decimal("101"),
        low_price=Decimal("99"),
        close_price=Decimal("100.5"),
        volume=Decimal("10"),
        turnover=Decimal("1000"),
    )


def service(
    repository: FakeRepository,
    backfill: FakeBackfill,
) -> LiveMarketDataService:
    return LiveMarketDataService(
        stream=object(),  # type: ignore[arg-type]
        backfill=backfill,  # type: ignore[arg-type]
        universe_refresh=object(),  # type: ignore[arg-type]
        session_factory=object(),  # type: ignore[arg-type]
        reconciliation_interval_seconds=60,
        repository=repository,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_live_gap_triggers_synchronous_recovery() -> None:
    repository = FakeRepository(ingest_result="gap")
    backfill = FakeBackfill()

    await service(repository, backfill)._handle_event(
        ClosedCandleEvent(candle=candle(), received_at=datetime.now(UTC))
    )

    assert backfill.calls == 1


@pytest.mark.asyncio
async def test_disconnect_marks_shard_degraded() -> None:
    repository = FakeRepository()
    backfill = FakeBackfill()

    await service(repository, backfill)._handle_event(
        ShardDisconnectedEvent(symbols=("BTCUSDT", "ETHUSDT"), reason="network")
    )

    assert repository.states == [(("BTCUSDT", "ETHUSDT"), "degraded", "network")]
    assert backfill.calls == 0


@pytest.mark.asyncio
async def test_reconnect_marks_recovering_and_backfills_before_consuming_more() -> None:
    repository = FakeRepository()
    backfill = FakeBackfill()

    await service(repository, backfill)._handle_event(ShardReconnectedEvent(symbols=("BTCUSDT",)))

    assert repository.states == [(("BTCUSDT",), "recovering", None)]
    assert backfill.calls == 1
