from datetime import datetime
from typing import Protocol

from crypto_smc.providers.models import Candle1m, Instrument, MarketAsset, MarketTicker


class InstrumentProvider(Protocol):
    async def list_usdt_perpetual_instruments(self) -> list[Instrument]: ...

    async def close(self) -> None: ...


class MarketTickerProvider(Protocol):
    async def list_linear_tickers(self) -> dict[str, MarketTicker]: ...

    async def close(self) -> None: ...


class RankingProvider(Protocol):
    async def list_top_assets(self, limit: int) -> list[MarketAsset]: ...

    async def close(self) -> None: ...


class KlineProvider(Protocol):
    async def server_time_ms(self) -> int: ...

    async def get_closed_1m_klines(
        self,
        *,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        limit: int,
    ) -> list[Candle1m]: ...

    async def close(self) -> None: ...
