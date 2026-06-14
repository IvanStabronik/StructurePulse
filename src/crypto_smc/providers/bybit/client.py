from decimal import Decimal
from time import monotonic
from typing import Any

import httpx
import structlog

from crypto_smc.observability.metrics import BYBIT_REQUEST_DURATION, BYBIT_REQUESTS
from crypto_smc.providers.bybit.schemas import (
    BybitInstrument,
    BybitInstrumentResponse,
    BybitTickerResponse,
)
from crypto_smc.providers.models import Instrument, MarketTicker

logger = structlog.get_logger(__name__)


class BybitAPIError(RuntimeError):
    """Raised when Bybit returns an invalid or unsuccessful response."""


class BybitClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        instrument_page_size: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._page_size = instrument_page_size
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "crypto-smc-bot/0.1.0"},
        )

    async def list_usdt_perpetual_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        cursor = ""

        while True:
            params: dict[str, str | int] = {
                "category": "linear",
                "status": "Trading",
                "limit": self._page_size,
            }
            if cursor:
                params["cursor"] = cursor

            payload = await self._get("/v5/market/instruments-info", params=params)
            response = BybitInstrumentResponse.model_validate(payload)
            if response.retCode != 0:
                raise BybitAPIError(f"Bybit error {response.retCode}: {response.retMsg}")
            if response.result.category != "linear":
                raise BybitAPIError(f"Unexpected category: {response.result.category}")

            instruments.extend(
                self._normalize(item)
                for item in response.result.list
                if self._is_usdt_perpetual(item)
            )

            cursor = response.result.nextPageCursor
            if not cursor:
                break

        return sorted(instruments, key=lambda instrument: instrument.symbol)

    async def server_time_ms(self) -> int:
        payload = await self._get("/v5/market/time")
        ret_code = payload.get("retCode")
        if ret_code != 0:
            raise BybitAPIError(f"Bybit error {ret_code}: {payload.get('retMsg')}")
        result = payload.get("result")
        if not isinstance(result, dict) or "timeNano" not in result:
            raise BybitAPIError("Bybit time response is missing result.timeNano")
        return int(str(result["timeNano"])) // 1_000_000

    async def list_linear_tickers(self) -> dict[str, MarketTicker]:
        payload = await self._get("/v5/market/tickers", params={"category": "linear"})
        response = BybitTickerResponse.model_validate(payload)
        if response.retCode != 0:
            raise BybitAPIError(f"Bybit error {response.retCode}: {response.retMsg}")
        if response.result.category != "linear":
            raise BybitAPIError(f"Unexpected category: {response.result.category}")

        return {
            item.symbol: MarketTicker(
                symbol=item.symbol,
                last_price=self._decimal_or_zero(item.lastPrice),
                mark_price=self._decimal_or_zero(item.markPrice),
                bid_price=self._decimal_or_zero(item.bid1Price),
                ask_price=self._decimal_or_zero(item.ask1Price),
                turnover_24h=self._decimal_or_zero(item.turnover24h),
                volume_24h=self._decimal_or_zero(item.volume24h),
                open_interest=self._decimal_or_zero(item.openInterest),
                open_interest_value=self._decimal_or_zero(item.openInterestValue),
                funding_rate=self._decimal_or_zero(item.fundingRate),
            )
            for item in response.result.list
        }

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def _get(
        self,
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        started_at = monotonic()
        try:
            response = await self._http.get(endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise BybitAPIError("Bybit returned a non-object JSON response")
        except (httpx.HTTPError, ValueError) as exc:
            BYBIT_REQUESTS.labels(endpoint=endpoint, outcome="error").inc()
            await logger.aexception("bybit_request_failed", endpoint=endpoint)
            raise BybitAPIError(f"Bybit request failed: {endpoint}") from exc
        else:
            BYBIT_REQUESTS.labels(endpoint=endpoint, outcome="success").inc()
            return payload
        finally:
            BYBIT_REQUEST_DURATION.labels(endpoint=endpoint).observe(monotonic() - started_at)

    @staticmethod
    def _is_usdt_perpetual(item: BybitInstrument) -> bool:
        return (
            item.status == "Trading"
            and item.contractType == "LinearPerpetual"
            and item.quoteCoin == "USDT"
            and item.settleCoin == "USDT"
        )

    @staticmethod
    def _normalize(item: BybitInstrument) -> Instrument:
        return Instrument(
            symbol=item.symbol,
            base_coin=item.baseCoin,
            quote_coin=item.quoteCoin,
            settle_coin=item.settleCoin,
            status="Trading",
            contract_type="LinearPerpetual",
            launch_time=Instrument.timestamp_ms_to_datetime(item.launchTime),
            tick_size=Decimal(item.priceFilter.tickSize),
            min_price=Decimal(item.priceFilter.minPrice),
            max_price=Decimal(item.priceFilter.maxPrice),
            quantity_step=Decimal(item.lotSizeFilter.qtyStep),
            min_order_quantity=Decimal(item.lotSizeFilter.minOrderQty),
            max_order_quantity=Decimal(item.lotSizeFilter.maxOrderQty),
            max_market_order_quantity=Decimal(item.lotSizeFilter.maxMktOrderQty),
            min_notional_value=Decimal(item.lotSizeFilter.minNotionalValue),
            min_leverage=Decimal(item.leverageFilter.minLeverage),
            max_leverage=Decimal(item.leverageFilter.maxLeverage),
            leverage_step=Decimal(item.leverageFilter.leverageStep),
            funding_interval_minutes=item.fundingInterval,
        )

    @staticmethod
    def _decimal_or_zero(value: str) -> Decimal:
        return Decimal(value) if value else Decimal(0)
