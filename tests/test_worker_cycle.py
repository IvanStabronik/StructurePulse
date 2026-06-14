import pytest

from crypto_smc.services.worker_cycle import WorkerCycle


class FakeUniverseRefresh:
    def __init__(self) -> None:
        self.calls = 0

    async def refresh(self) -> int:
        self.calls += 1
        return self.calls


class FakeMarketDataSync:
    def __init__(self) -> None:
        self.calls = 0

    async def sync_once(self) -> dict[str, int]:
        self.calls += 1
        return {"ready": 0, "recovered": 0, "failed": 0}


@pytest.mark.asyncio
async def test_worker_cycle_refreshes_universe_once_and_market_data_each_cycle() -> None:
    universe = FakeUniverseRefresh()
    market_data = FakeMarketDataSync()
    cycle = WorkerCycle(
        universe_refresh=universe,  # type: ignore[arg-type]
        market_data_sync=market_data,  # type: ignore[arg-type]
        universe_refresh_interval_seconds=10**18,
    )

    await cycle.run_once()
    await cycle.run_once()

    assert universe.calls == 1
    assert market_data.calls == 2
