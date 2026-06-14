import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import Any

import httpx
import structlog

from crypto_smc.observability.metrics import (
    BYBIT_REQUEST_DURATION,
    BYBIT_REQUESTS,
)
from crypto_smc.providers.bybit.rate_limit import AdaptiveRateLimiter
from crypto_smc.providers.bybit.schemas import (
    BybitInstrument,
    BybitInstrumentResponse,
    BybitKlineResponse,
    BybitTickerResponse,
)
from crypto_smc.providers.models import Candle1m, Instrument, MarketTicker

logger = structlog.get_logger(__name__)


class BybitAPIError(RuntimeError):
    """Raised when Bybit returns an invalid or unsuccessful response."""


class BybitRateLimitError(BybitAPIError):
    """Raised after bounded Bybit rate-limit retries are exhausted."""


class BybitClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        instrument_page_size: int,
        max_requests_per_second: float = 8,
        max_concurrency: int = 4,
        max_retries: int = 5,
        retry_base_seconds: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: AdaptiveRateLimiter | None = None,
    ) -> None:
        self._page_size = instrument_page_size
        self._max_retries = max_retries
        self._owns_http_client = http_client is None
        self._rate_limiter = rate_limiter or AdaptiveRateLimiter(
            requests_per_second=max_requests_per_second,
            max_concurrency=max_concurrency,
            retry_base_seconds=retry_base_seconds,
        )
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

    async def get_closed_1m_klines(
        self,
        *,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        limit: int,
    ) -> list[Candle1m]:
        return await self.get_klines(
            symbol=symbol,
            interval="1",
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    async def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        limit: int,
    ) -> list[Candle1m]:
        if not 1 <= limit <= 1000:
            raise ValueError("Bybit kline limit must be between 1 and 1000")
        start_ms = self._datetime_to_ms(start_time)
        end_ms = self._datetime_to_ms(end_time)
        payload = await self._get(
            "/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol.upper(),
                "interval": interval,
                "start": start_ms,
                "end": end_ms,
                "limit": limit,
            },
        )
        response = BybitKlineResponse.model_validate(payload)
        if response.retCode != 0:
            raise BybitAPIError(f"Bybit error {response.retCode}: {response.retMsg}")
        if response.result.category != "linear":
            raise BybitAPIError(f"Unexpected category: {response.result.category}")
        if response.result.symbol != symbol.upper():
            raise BybitAPIError(f"Unexpected kline symbol: {response.result.symbol}")

        candles = [
            self._normalize_kline(symbol.upper(), values)
            for values in response.result.list
            if start_ms <= int(values[0]) <= end_ms
        ]
        return sorted(candles, key=lambda candle: candle.open_time)

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def _get(
        self,
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            started_at = monotonic()
            try:
                async with self._rate_limiter.request_slot():
                    response = await self._http.get(endpoint, params=params)
                await self._rate_limiter.observe_headers(response.headers)
                ip_ban = response.status_code == 403
                http_rate_limited = response.status_code == 429
                try:
                    payload = response.json()
                except ValueError:
                    if not (ip_ban or http_rate_limited):
                        raise
                    payload = {}
                if not isinstance(payload, dict):
                    raise BybitAPIError("Bybit returned a non-object JSON response")

                ret_code = payload.get("retCode")
                rate_limited = http_rate_limited or ret_code == 10006
                if rate_limited or ip_ban:
                    if attempt >= self._max_retries:
                        BYBIT_REQUESTS.labels(endpoint=endpoint, outcome="rate_limited").inc()
                        raise BybitRateLimitError(f"Bybit rate limit retries exhausted: {endpoint}")
                    reason = "ip_ban" if ip_ban else "rate_limit"
                    delay = self._rate_limiter.retry_delay(
                        attempt=attempt,
                        headers=response.headers,
                        ip_ban=ip_ban,
                    )
                    await self._rate_limiter.block_for(delay, reason=reason)
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
            except BybitRateLimitError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                BYBIT_REQUESTS.labels(endpoint=endpoint, outcome="error").inc()
                await logger.aexception("bybit_request_failed", endpoint=endpoint)
                raise BybitAPIError(f"Bybit request failed: {endpoint}") from exc
            else:
                BYBIT_REQUESTS.labels(endpoint=endpoint, outcome="success").inc()
                return payload
            finally:
                BYBIT_REQUEST_DURATION.labels(endpoint=endpoint).observe(monotonic() - started_at)

        raise BybitRateLimitError(f"Bybit retries exhausted: {endpoint}")

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

    @staticmethod
    def _datetime_to_ms(value: datetime) -> int:
        if value.tzinfo is None:
            raise ValueError("Bybit timestamps must be timezone-aware")
        return int(value.timestamp() * 1000)

    @staticmethod
    def _normalize_kline(symbol: str, values: list[str]) -> Candle1m:
        if len(values) < 7:
            raise BybitAPIError("Bybit kline row has fewer than 7 values")
        return Candle1m(
            symbol=symbol,
            open_time=datetime.fromtimestamp(int(values[0]) / 1000, tz=UTC),
            open_price=Decimal(values[1]),
            high_price=Decimal(values[2]),
            low_price=Decimal(values[3]),
            close_price=Decimal(values[4]),
            volume=Decimal(values[5]),
            turnover=Decimal(values[6]),
        )
