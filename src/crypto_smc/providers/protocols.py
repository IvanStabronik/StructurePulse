from typing import Protocol

from crypto_smc.providers.models import Instrument, MarketAsset, MarketTicker


class InstrumentProvider(Protocol):
    async def list_usdt_perpetual_instruments(self) -> list[Instrument]: ...

    async def close(self) -> None: ...


class MarketTickerProvider(Protocol):
    async def list_linear_tickers(self) -> dict[str, MarketTicker]: ...

    async def close(self) -> None: ...


class RankingProvider(Protocol):
    async def list_top_assets(self, limit: int) -> list[MarketAsset]: ...

    async def close(self) -> None: ...
