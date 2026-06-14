from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CoinGeckoMarketAsset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    symbol: str
    name: str
    market_cap_rank: int | None
    market_cap: Decimal | None
    total_volume: Decimal | None
    current_price: Decimal | None
    last_updated: datetime | None = None
