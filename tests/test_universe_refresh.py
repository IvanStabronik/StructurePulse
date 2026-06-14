from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.universe import UniverseRepository
from crypto_smc.providers.coingecko.client import CoinGeckoAPIError
from crypto_smc.providers.protocols import (
    InstrumentProvider,
    MarketTickerProvider,
    RankingProvider,
)
from crypto_smc.services.universe_refresh import UniverseRefreshService
from crypto_smc.universe import UniversePolicyConfig


class FailingProvider:
    async def list_top_assets(self, limit: int) -> list[object]:
        raise CoinGeckoAPIError(str(limit))

    async def close(self) -> None:
        return None


class EmptyBybitProvider:
    async def list_usdt_perpetual_instruments(self) -> list[object]:
        return []

    async def list_linear_tickers(self) -> dict[str, object]:
        return {}

    async def close(self) -> None:
        return None


class FakeUniverseRepository:
    @asynccontextmanager
    async def refresh_lock(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[bool]:
        del session_factory
        yield True


@pytest.mark.asyncio
async def test_provider_failure_preserves_previous_universe_without_db_write() -> None:
    bybit = EmptyBybitProvider()
    service = UniverseRefreshService(
        instrument_provider=cast(InstrumentProvider, bybit),
        ticker_provider=cast(MarketTickerProvider, bybit),
        ranking_provider=cast(RankingProvider, FailingProvider()),
        session_factory=cast(async_sessionmaker[AsyncSession], object()),
        universe_repository=cast(UniverseRepository, FakeUniverseRepository()),
        policy_config=UniversePolicyConfig(
            size=30,
            min_turnover_24h_usdt=0,
            max_spread_bps=20,
            min_trading_history_days=30,
        ),
        ranking_fetch_limit=150,
    )

    assert await service.refresh() is None
