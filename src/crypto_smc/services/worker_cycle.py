from time import monotonic

from crypto_smc.market_data import MarketDataBackfillService
from crypto_smc.services.universe_refresh import UniverseRefreshService


class WorkerCycle:
    def __init__(
        self,
        *,
        universe_refresh: UniverseRefreshService,
        market_data_sync: MarketDataBackfillService,
        universe_refresh_interval_seconds: float = 24 * 60 * 60,
    ) -> None:
        self._universe_refresh = universe_refresh
        self._market_data_sync = market_data_sync
        self._universe_refresh_interval_seconds = universe_refresh_interval_seconds
        self._last_universe_attempt: float | None = None

    async def run_once(self) -> None:
        now = monotonic()
        refresh_due = (
            self._last_universe_attempt is None
            or now - self._last_universe_attempt >= self._universe_refresh_interval_seconds
        )
        if refresh_due:
            self._last_universe_attempt = now
            await self._universe_refresh.refresh()
        await self._market_data_sync.sync_once()
