import asyncio
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Self

from crypto_smc.observability.metrics import (
    STRATEGY_PROCESS_ACTIVE_BATCHES,
    STRATEGY_PROCESS_SATURATION_RATIO,
    STRATEGY_PROCESS_WAITING_BATCHES,
)
from smc_core import Candle, SMCAnalysis, SMCConfig, analyze

type AnalysisRequest = tuple[tuple[Candle, ...], SMCConfig]


class AnalysisProcessPool:
    def __init__(self, *, max_workers: int, max_pending_batches: int) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if max_pending_batches < 1:
            raise ValueError("max_pending_batches must be positive")
        self._executor = ProcessPoolExecutor(max_workers=max_workers)
        self._capacity = asyncio.Semaphore(max_pending_batches)
        self._max_pending_batches = max_pending_batches
        self._active_batches = 0
        self._waiting_batches = 0

    async def analyze_batch(
        self,
        requests: Sequence[AnalysisRequest],
    ) -> tuple[SMCAnalysis, ...]:
        if not requests:
            return ()

        self._waiting_batches += 1
        self._update_metrics()
        await self._capacity.acquire()
        self._waiting_batches -= 1
        self._active_batches += 1
        self._update_metrics()
        try:
            loop = asyncio.get_running_loop()
            futures = [
                loop.run_in_executor(self._executor, _analyze_request, request)
                for request in requests
            ]
            return tuple(await asyncio.gather(*futures))
        finally:
            self._active_batches -= 1
            self._capacity.release()
            self._update_metrics()

    async def close(self) -> None:
        await asyncio.to_thread(self._executor.shutdown, True, cancel_futures=True)

    def _update_metrics(self) -> None:
        STRATEGY_PROCESS_ACTIVE_BATCHES.set(self._active_batches)
        STRATEGY_PROCESS_WAITING_BATCHES.set(self._waiting_batches)
        STRATEGY_PROCESS_SATURATION_RATIO.set(self._active_batches / self._max_pending_batches)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        await self.close()


def _analyze_request(request: AnalysisRequest) -> SMCAnalysis:
    candles, config = request
    return analyze(candles, config)
