from time import monotonic
from typing import Any

import httpx
import structlog

from crypto_smc.observability.metrics import (
    COINGECKO_REQUEST_DURATION,
    COINGECKO_REQUESTS,
)
from crypto_smc.providers.coingecko.schemas import CoinGeckoMarketAsset
from crypto_smc.providers.models import MarketAsset

logger = structlog.get_logger(__name__)


class CoinGeckoAPIError(RuntimeError):
    """Raised when CoinGecko returns an invalid or unsuccessful response."""


class CoinGeckoClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        api_key: str | None = None,
        api_key_type: str = "demo",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {"User-Agent": "crypto-smc-bot/0.1.0"}
        if api_key:
            header_name = "x-cg-pro-api-key" if api_key_type == "pro" else "x-cg-demo-api-key"
            headers[header_name] = api_key

        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers=headers,
        )

    async def list_top_assets(self, limit: int) -> list[MarketAsset]:
        if not 1 <= limit <= 250:
            raise ValueError("CoinGecko market limit must be between 1 and 250")

        payload = await self._get(
            "/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": limit,
                "page": 1,
                "sparkline": "false",
            },
        )
        if not isinstance(payload, list):
            raise CoinGeckoAPIError("CoinGecko returned a non-list JSON response")

        assets: list[MarketAsset] = []
        for raw_item in payload:
            item = CoinGeckoMarketAsset.model_validate(raw_item)
            if item.market_cap_rank is None:
                continue
            assets.append(
                MarketAsset(
                    provider_id=item.id,
                    symbol=item.symbol.upper(),
                    name=item.name,
                    market_cap_rank=item.market_cap_rank,
                    market_cap_usd=item.market_cap or 0,
                    total_volume_usd=item.total_volume or 0,
                    current_price_usd=item.current_price or 0,
                    last_updated=item.last_updated,
                )
            )
        return sorted(assets, key=lambda asset: asset.market_cap_rank)

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def _get(
        self,
        endpoint: str,
        *,
        params: dict[str, str | int],
    ) -> Any:
        started_at = monotonic()
        try:
            response = await self._http.get(endpoint, params=params)
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            COINGECKO_REQUESTS.labels(endpoint=endpoint, outcome="error").inc()
            await logger.aexception("coingecko_request_failed", endpoint=endpoint)
            raise CoinGeckoAPIError(f"CoinGecko request failed: {endpoint}") from exc
        else:
            COINGECKO_REQUESTS.labels(endpoint=endpoint, outcome="success").inc()
            return payload
        finally:
            COINGECKO_REQUEST_DURATION.labels(endpoint=endpoint).observe(monotonic() - started_at)
