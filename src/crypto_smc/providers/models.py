from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    base_coin: str
    quote_coin: str
    settle_coin: str
    status: Literal["Trading"]
    contract_type: Literal["LinearPerpetual"]
    launch_time: datetime
    tick_size: Decimal
    min_price: Decimal
    max_price: Decimal
    quantity_step: Decimal
    min_order_quantity: Decimal
    max_order_quantity: Decimal
    max_market_order_quantity: Decimal
    min_notional_value: Decimal
    min_leverage: Decimal
    max_leverage: Decimal
    leverage_step: Decimal
    funding_interval_minutes: int

    @classmethod
    def timestamp_ms_to_datetime(cls, value: str) -> datetime:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


class MarketAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    symbol: str
    name: str
    market_cap_rank: int
    market_cap_usd: Decimal
    total_volume_usd: Decimal
    current_price_usd: Decimal
    last_updated: datetime | None


class MarketTicker(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    last_price: Decimal
    mark_price: Decimal
    bid_price: Decimal
    ask_price: Decimal
    turnover_24h: Decimal
    volume_24h: Decimal
    open_interest: Decimal
    open_interest_value: Decimal
    funding_rate: Decimal

    @property
    def spread_bps(self) -> Decimal | None:
        if self.bid_price <= 0 or self.ask_price <= 0:
            return None
        midpoint = (self.bid_price + self.ask_price) / Decimal(2)
        if midpoint <= 0:
            return None
        return (self.ask_price - self.bid_price) / midpoint * Decimal(10_000)


class Candle1m(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    open_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    turnover: Decimal


class PublicTrade(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    symbol: str
    price: Decimal
    size: Decimal
    side: Literal["Buy", "Sell"]
    executed_at: datetime
    sequence: int
    is_block_trade: bool = False
    is_rpi_trade: bool = False
