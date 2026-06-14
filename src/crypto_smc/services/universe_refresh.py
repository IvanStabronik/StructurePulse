import asyncio
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.repositories.instruments import InstrumentRepository
from crypto_smc.db.repositories.universe import UniverseRepository
from crypto_smc.observability.metrics import UNIVERSE_REFRESHES
from crypto_smc.providers.models import MarketAsset
from crypto_smc.providers.protocols import (
    InstrumentProvider,
    MarketTickerProvider,
    RankingProvider,
)
from crypto_smc.universe import UniversePolicy, UniversePolicyConfig

logger = structlog.get_logger(__name__)


class UniverseRefreshService:
    def __init__(
        self,
        *,
        instrument_provider: InstrumentProvider,
        ticker_provider: MarketTickerProvider,
        ranking_provider: RankingProvider,
        session_factory: async_sessionmaker[AsyncSession],
        policy_config: UniversePolicyConfig,
        ranking_fetch_limit: int,
        instrument_repository: InstrumentRepository | None = None,
        universe_repository: UniverseRepository | None = None,
    ) -> None:
        self._instrument_provider = instrument_provider
        self._ticker_provider = ticker_provider
        self._ranking_provider = ranking_provider
        self._session_factory = session_factory
        self._policy = UniversePolicy(policy_config)
        self._policy_config = policy_config
        self._ranking_fetch_limit = ranking_fetch_limit
        self._instrument_repository = instrument_repository or InstrumentRepository()
        self._universe_repository = universe_repository or UniverseRepository()

    async def refresh(self) -> int | None:
        async with self._universe_repository.refresh_lock(self._session_factory) as locked:
            if not locked:
                UNIVERSE_REFRESHES.labels(outcome="lock_busy").inc()
                await logger.awarning("universe_refresh_skipped_lock_busy")
                return None

            try:
                instruments, tickers, assets = await asyncio.gather(
                    self._instrument_provider.list_usdt_perpetual_instruments(),
                    self._ticker_provider.list_linear_tickers(),
                    self._ranking_provider.list_top_assets(self._ranking_fetch_limit),
                )
            except Exception:
                UNIVERSE_REFRESHES.labels(outcome="provider_error").inc()
                await logger.aexception("universe_provider_failed_preserving_previous")
                return None

            async with self._session_factory() as session, session.begin():
                await self._instrument_repository.replace_active_set(session, instruments)

            decisions = self._policy.evaluate(
                assets=assets,
                instruments=instruments,
                tickers=tickers,
            )
            source_updated_at = self._latest_source_update(assets)
            snapshot_id = await self._universe_repository.save_snapshot(
                session_factory=self._session_factory,
                decisions=decisions,
                source="coingecko",
                configuration=self._configuration_snapshot(),
                source_updated_at=source_updated_at,
            )

        selected_count = sum(decision.is_selected for decision in decisions)
        UNIVERSE_REFRESHES.labels(outcome="success").inc()
        await logger.ainfo(
            "universe_refreshed",
            snapshot_id=snapshot_id,
            candidates=len(decisions),
            selected=selected_count,
        )
        return snapshot_id

    def _configuration_snapshot(self) -> dict[str, Any]:
        return {
            "size": self._policy_config.size,
            "ranking_fetch_limit": self._ranking_fetch_limit,
            "min_turnover_24h_usdt": str(self._policy_config.min_turnover_24h_usdt),
            "max_spread_bps": str(self._policy_config.max_spread_bps),
            "min_trading_history_days": self._policy_config.min_trading_history_days,
            "manual_denylist": sorted(self._policy_config.manual_denylist),
        }

    @staticmethod
    def _latest_source_update(assets: list[MarketAsset]) -> datetime | None:
        timestamps = [asset.last_updated for asset in assets if asset.last_updated]
        return max(timestamps) if timestamps else None
