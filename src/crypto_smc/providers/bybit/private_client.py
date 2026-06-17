import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from crypto_smc.providers.bybit.client import BybitAPIError
from crypto_smc.providers.bybit.rate_limit import AdaptiveRateLimiter


class BybitPrivateAPIError(BybitAPIError):
    """Raised when a signed Bybit V5 private endpoint fails."""


@dataclass(frozen=True)
class WalletCoinBalance:
    coin: str
    wallet_balance: Decimal
    available_to_withdraw: Decimal | None
    equity: Decimal
    usd_value: Decimal


@dataclass(frozen=True)
class WalletBalance:
    account_type: str
    total_available_balance: Decimal
    total_wallet_balance: Decimal
    coins: tuple[WalletCoinBalance, ...]


@dataclass(frozen=True)
class BybitOrderResult:
    order_id: str
    order_link_id: str


@dataclass(frozen=True)
class BybitPosition:
    symbol: str
    side: str
    size: Decimal
    average_price: Decimal


class BybitPrivateClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        api_secret: str,
        timeout_seconds: float,
        recv_window_ms: int = 5000,
        max_requests_per_second: float = 4,
        max_concurrency: int = 2,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: AdaptiveRateLimiter | None = None,
        timestamp_ms: Callable[[], int] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Bybit API key is required for private endpoints")
        if not api_secret:
            raise ValueError("Bybit API secret is required for private endpoints")

        self._api_key = api_key
        self._api_secret = api_secret
        self._recv_window_ms = recv_window_ms
        self._owns_http_client = http_client is None
        self._timestamp_ms = timestamp_ms or (lambda: int(time.time() * 1000))
        self._rate_limiter = rate_limiter or AdaptiveRateLimiter(
            requests_per_second=max_requests_per_second,
            max_concurrency=max_concurrency,
            retry_base_seconds=0.5,
        )
        self._http = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "structurepulse/0.1.0"},
        )

    async def get_wallet_balance(
        self,
        *,
        account_type: Literal["UNIFIED"] = "UNIFIED",
        coin: str = "USDT",
    ) -> WalletBalance:
        payload = await self._get(
            "/v5/account/wallet-balance",
            params={"accountType": account_type, "coin": coin.upper()},
        )
        result = self._result_object(payload)
        accounts = result.get("list")
        if not isinstance(accounts, list) or not accounts:
            raise BybitPrivateAPIError("Bybit wallet response is missing result.list")
        account = accounts[0]
        if not isinstance(account, dict):
            raise BybitPrivateAPIError("Bybit wallet account is not an object")

        coins = account.get("coin")
        if not isinstance(coins, list):
            raise BybitPrivateAPIError("Bybit wallet response is missing account.coin")

        return WalletBalance(
            account_type=str(account.get("accountType", "")),
            total_available_balance=_decimal(account.get("totalAvailableBalance")),
            total_wallet_balance=_decimal(account.get("totalWalletBalance")),
            coins=tuple(_coin_balance(item) for item in coins if isinstance(item, dict)),
        )

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: Literal["Buy", "Sell"],
        qty: Decimal,
        order_link_id: str,
        reduce_only: bool = False,
        position_idx: int = 0,
    ) -> BybitOrderResult:
        payload = await self._post(
            "/v5/order/create",
            body={
                "category": "linear",
                "symbol": symbol.upper(),
                "side": side,
                "orderType": "Market",
                "qty": _format_decimal(qty),
                "positionIdx": position_idx,
                "reduceOnly": reduce_only,
                "orderLinkId": order_link_id,
            },
        )
        result = self._result_object(payload)
        return BybitOrderResult(
            order_id=str(result.get("orderId", "")),
            order_link_id=str(result.get("orderLinkId", "")),
        )

    async def get_linear_position(self, *, symbol: str) -> BybitPosition | None:
        payload = await self._get(
            "/v5/position/list",
            params={"category": "linear", "symbol": symbol.upper()},
        )
        result = self._result_object(payload)
        positions = result.get("list")
        if not isinstance(positions, list):
            raise BybitPrivateAPIError("Bybit position response is missing result.list")
        for item in positions:
            if not isinstance(item, dict):
                continue
            size = _decimal(item.get("size"))
            if size <= 0:
                continue
            return BybitPosition(
                symbol=str(item.get("symbol", "")).upper(),
                side=str(item.get("side", "")),
                size=size,
                average_price=_decimal(item.get("avgPrice")),
            )
        return None

    async def set_full_position_stop(
        self,
        *,
        symbol: str,
        stop_loss: Decimal,
        position_idx: int = 0,
    ) -> None:
        await self._post(
            "/v5/position/trading-stop",
            body={
                "category": "linear",
                "symbol": symbol.upper(),
                "tpslMode": "Full",
                "stopLoss": _format_decimal(stop_loss),
                "slOrderType": "Market",
                "positionIdx": position_idx,
            },
        )

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def _get(self, endpoint: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        query = urlencode(params or {})
        headers = self._signed_headers(query)
        async with self._rate_limiter.request_slot():
            response = await self._http.get(endpoint, params=params, headers=headers)
        return self._validate_response(endpoint, response)

    async def _post(self, endpoint: str, *, body: dict[str, object]) -> dict[str, Any]:
        serialized = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        headers = self._signed_headers(serialized)
        async with self._rate_limiter.request_slot():
            response = await self._http.post(endpoint, content=serialized, headers=headers)
        return self._validate_response(endpoint, response)

    def _signed_headers(self, payload: str) -> dict[str, str]:
        timestamp = str(self._timestamp_ms())
        recv_window = str(self._recv_window_ms)
        signing_payload = f"{timestamp}{self._api_key}{recv_window}{payload}"
        signature = hmac.new(
            self._api_secret.encode(),
            signing_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
        }

    @staticmethod
    def _validate_response(endpoint: str, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BybitPrivateAPIError(f"Bybit private response is not JSON: {endpoint}") from exc
        if not isinstance(payload, dict):
            raise BybitPrivateAPIError(f"Bybit private response is not an object: {endpoint}")

        response.raise_for_status()
        ret_code = payload.get("retCode")
        if ret_code != 0:
            raise BybitPrivateAPIError(f"Bybit private error {ret_code}: {payload.get('retMsg')}")
        return payload

    @staticmethod
    def _result_object(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise BybitPrivateAPIError("Bybit private response is missing result object")
        return result


def _decimal(value: object) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    return Decimal(str(value))


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _coin_balance(payload: dict[str, Any]) -> WalletCoinBalance:
    return WalletCoinBalance(
        coin=str(payload.get("coin", "")),
        wallet_balance=_decimal(payload.get("walletBalance")),
        available_to_withdraw=_optional_decimal(payload.get("availableToWithdraw")),
        equity=_decimal(payload.get("equity")),
        usd_value=_decimal(payload.get("usdValue")),
    )


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")
