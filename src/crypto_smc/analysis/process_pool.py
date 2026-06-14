import asyncio
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Self

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

    async def analyze_batch(
        self,
        requests: Sequence[AnalysisRequest],
    ) -> tuple[SMCAnalysis, ...]:
        if not requests:
            return ()

        async with self._capacity:
            loop = asyncio.get_running_loop()
            futures = [
                loop.run_in_executor(self._executor, _analyze_request, request)
                for request in requests
            ]
            return tuple(await asyncio.gather(*futures))

    async def close(self) -> None:
        await asyncio.to_thread(self._executor.shutdown, True, cancel_futures=True)

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
