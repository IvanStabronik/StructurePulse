import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from crypto_smc.providers.bybit import BybitPrivateClient


def signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_get_wallet_balance_signs_query_and_normalizes_response() -> None:
    api_key = "test-key"
    api_secret = "test-secret"
    timestamp = 1_672_211_928_338
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        assert request.url.path == "/v5/account/wallet-balance"
        assert request.url.query.decode() == "accountType=UNIFIED&coin=USDT"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "accountType": "UNIFIED",
                            "totalAvailableBalance": "250.5",
                            "totalWalletBalance": "251",
                            "coin": [
                                {
                                    "coin": "USDT",
                                    "walletBalance": "251",
                                    "availableToWithdraw": "",
                                    "equity": "250.5",
                                    "usdValue": "250.5",
                                }
                            ],
                        }
                    ]
                },
                "time": timestamp,
            },
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitPrivateClient(
        base_url="https://unused.test",
        api_key=api_key,
        api_secret=api_secret,
        timeout_seconds=1,
        http_client=http_client,
        timestamp_ms=lambda: timestamp,
    )

    balance = await client.get_wallet_balance()
    await http_client.aclose()

    expected_payload = f"{timestamp}{api_key}5000accountType=UNIFIED&coin=USDT"
    assert seen_headers["x-bapi-sign"] == signature(api_secret, expected_payload)
    assert balance.total_available_balance == Decimal("250.5")
    assert balance.coins[0].available_to_withdraw is None


@pytest.mark.asyncio
async def test_place_market_order_signs_body_and_returns_order_ids() -> None:
    api_key = "test-key"
    api_secret = "test-secret"
    timestamp = 1_672_211_928_338
    seen_body = ""
    seen_signature = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body, seen_signature
        seen_body = request.content.decode()
        seen_signature = request.headers["x-bapi-sign"]
        assert request.url.path == "/v5/order/create"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {"orderId": "abc", "orderLinkId": "sp-1-entry"},
                "time": timestamp,
            },
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitPrivateClient(
        base_url="https://unused.test",
        api_key=api_key,
        api_secret=api_secret,
        timeout_seconds=1,
        http_client=http_client,
        timestamp_ms=lambda: timestamp,
    )

    result = await client.place_market_order(
        symbol="btcusdt",
        side="Buy",
        qty=Decimal("0.0010"),
        order_link_id="sp-1-entry",
    )
    await http_client.aclose()

    assert json.loads(seen_body) == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderType": "Market",
        "qty": "0.001",
        "positionIdx": 0,
        "reduceOnly": False,
        "orderLinkId": "sp-1-entry",
    }
    expected_payload = f"{timestamp}{api_key}5000{seen_body}"
    assert seen_signature == signature(api_secret, expected_payload)
    assert result.order_id == "abc"
    assert result.order_link_id == "sp-1-entry"


@pytest.mark.asyncio
async def test_set_linear_leverage_uses_position_endpoint() -> None:
    seen_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body
        seen_body = json.loads(request.content.decode())
        assert request.url.path == "/v5/position/set-leverage"
        return httpx.Response(
            200,
            json={"retCode": 0, "retMsg": "OK", "result": {}, "time": 1},
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitPrivateClient(
        base_url="https://unused.test",
        api_key="test-key",
        api_secret="test-secret",
        timeout_seconds=1,
        http_client=http_client,
        timestamp_ms=lambda: 1,
    )

    await client.set_linear_leverage(symbol="ethusdt", leverage=Decimal("20"))
    await http_client.aclose()

    assert seen_body == {
        "category": "linear",
        "symbol": "ETHUSDT",
        "buyLeverage": "20",
        "sellLeverage": "20",
    }


@pytest.mark.asyncio
async def test_set_full_position_stop_uses_trading_stop_endpoint() -> None:
    seen_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body
        seen_body = json.loads(request.content.decode())
        assert request.url.path == "/v5/position/trading-stop"
        return httpx.Response(
            200,
            json={"retCode": 0, "retMsg": "OK", "result": {}, "time": 1},
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitPrivateClient(
        base_url="https://unused.test",
        api_key="test-key",
        api_secret="test-secret",
        timeout_seconds=1,
        http_client=http_client,
        timestamp_ms=lambda: 1,
    )

    await client.set_full_position_stop(symbol="ethusdt", stop_loss=Decimal("2500.00"))
    await http_client.aclose()

    assert seen_body == {
        "category": "linear",
        "symbol": "ETHUSDT",
        "tpslMode": "Full",
        "stopLoss": "2500",
        "slOrderType": "Market",
        "positionIdx": 0,
    }


@pytest.mark.asyncio
async def test_get_linear_position_returns_open_position() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/position/list"
        assert request.url.query.decode() == "category=linear&symbol=BTCUSDT"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "side": "Buy",
                            "size": "0.25",
                            "avgPrice": "100",
                        }
                    ]
                },
                "time": 1,
            },
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitPrivateClient(
        base_url="https://unused.test",
        api_key="test-key",
        api_secret="test-secret",
        timeout_seconds=1,
        http_client=http_client,
        timestamp_ms=lambda: 1,
    )

    position = await client.get_linear_position(symbol="btcusdt")
    await http_client.aclose()

    assert position is not None
    assert position.symbol == "BTCUSDT"
    assert position.size == Decimal("0.25")
