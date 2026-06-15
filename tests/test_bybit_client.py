from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from crypto_smc.providers.bybit import BybitClient


def instrument_payload(
    symbol: str,
    *,
    quote_coin: str = "USDT",
    settle_coin: str = "USDT",
    contract_type: str = "LinearPerpetual",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "contractType": contract_type,
        "status": "Trading",
        "baseCoin": symbol.removesuffix(quote_coin),
        "quoteCoin": quote_coin,
        "launchTime": "1585526400000",
        "leverageFilter": {
            "minLeverage": "1",
            "maxLeverage": "100",
            "leverageStep": "0.01",
        },
        "priceFilter": {
            "minPrice": "0.1",
            "maxPrice": "1000000",
            "tickSize": "0.1",
        },
        "lotSizeFilter": {
            "minNotionalValue": "5",
            "maxOrderQty": "1000",
            "maxMktOrderQty": "500",
            "minOrderQty": "0.001",
            "qtyStep": "0.001",
        },
        "settleCoin": settle_coin,
        "fundingInterval": 480,
    }


def response_payload(
    items: list[dict[str, object]],
    *,
    cursor: str = "",
) -> dict[str, object]:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "category": "linear",
            "list": items,
            "nextPageCursor": cursor,
        },
        "time": 1735809771618,
    }


@pytest.mark.asyncio
async def test_list_instruments_paginates_filters_and_sorts() -> None:
    responses: Iterator[dict[str, object]] = iter(
        [
            response_payload(
                [
                    instrument_payload("ETHUSDT"),
                    instrument_payload("BTCUSDC", quote_coin="USDC", settle_coin="USDC"),
                ],
                cursor="next",
            ),
            response_payload(
                [
                    instrument_payload("BTCUSDT"),
                    instrument_payload("BTCUSDT-30JUN", contract_type="LinearFutures"),
                ]
            ),
        ]
    )
    requested_cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_cursors.append(request.url.params.get("cursor"))
        return httpx.Response(200, json=next(responses))

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitClient(
        base_url="https://unused.test",
        timeout_seconds=1,
        instrument_page_size=1000,
        http_client=http_client,
    )

    instruments = await client.list_usdt_perpetual_instruments()
    await http_client.aclose()

    assert [instrument.symbol for instrument in instruments] == ["BTCUSDT", "ETHUSDT"]
    assert requested_cursors == [None, "next"]
    assert instruments[0].max_leverage == 100


@pytest.mark.asyncio
async def test_server_time_converts_nanoseconds_to_milliseconds() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {"timeSecond": "1735809771", "timeNano": "1735809771123456789"},
                "time": 1735809771123,
            },
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitClient(
        base_url="https://unused.test",
        timeout_seconds=1,
        instrument_page_size=1000,
        http_client=http_client,
    )

    assert await client.server_time_ms() == 1735809771123
    await http_client.aclose()


@pytest.mark.asyncio
async def test_list_linear_tickers_normalizes_market_data() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "lastPrice": "100",
                            "markPrice": "100.1",
                            "openInterest": "50",
                            "openInterestValue": "5000",
                            "turnover24h": "100000000",
                            "volume24h": "1000000",
                            "fundingRate": "0.0001",
                            "bid1Price": "99.9",
                            "ask1Price": "100.1",
                        }
                    ],
                },
                "time": 1735809771123,
            },
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitClient(
        base_url="https://unused.test",
        timeout_seconds=1,
        instrument_page_size=1000,
        http_client=http_client,
    )

    tickers = await client.list_linear_tickers()
    await http_client.aclose()

    assert tickers["BTCUSDT"].turnover_24h == 100_000_000
    assert tickers["BTCUSDT"].spread_bps == 20


@pytest.mark.asyncio
async def test_get_closed_1m_klines_sorts_reverse_bybit_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "list": [
                        ["1735689660000", "101", "103", "100", "102", "12", "1200"],
                        ["1735689600000", "100", "102", "99", "101", "10", "1000"],
                    ],
                },
                "time": 1735689720000,
            },
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitClient(
        base_url="https://unused.test",
        timeout_seconds=1,
        instrument_page_size=1000,
        http_client=http_client,
    )

    candles = await client.get_closed_1m_klines(
        symbol="BTCUSDT",
        start_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
        end_time=datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
        limit=2,
    )
    await http_client.aclose()

    assert [candle.open_time.minute for candle in candles] == [0, 1]
    assert candles[1].close_price == 102


@pytest.mark.asyncio
async def test_get_recent_public_trades_normalizes_and_sorts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["category"] == "linear"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["limit"] == "2"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "category": "linear",
                    "list": [
                        {
                            "execId": "second",
                            "symbol": "BTCUSDT",
                            "price": "101",
                            "size": "2",
                            "side": "Sell",
                            "time": "1735689601000",
                            "isBlockTrade": False,
                            "isRPITrade": True,
                            "seq": "11",
                        },
                        {
                            "execId": "first",
                            "symbol": "BTCUSDT",
                            "price": "100",
                            "size": "1",
                            "side": "Buy",
                            "time": "1735689600000",
                            "isBlockTrade": False,
                            "isRPITrade": False,
                            "seq": "10",
                        },
                    ],
                },
                "time": 1735689602000,
            },
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitClient(
        base_url="https://unused.test",
        timeout_seconds=1,
        instrument_page_size=1000,
        http_client=http_client,
    )

    trades = await client.get_recent_public_trades(symbol="btcusdt", limit=2)
    await http_client.aclose()

    assert [trade.trade_id for trade in trades] == ["first", "second"]
    assert trades[0].price == 100
    assert trades[1].is_rpi_trade is True


@pytest.mark.asyncio
async def test_bybit_client_retries_http_429() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"retCode": 10006, "retMsg": "Too many visits!"},
            )
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {"timeSecond": "1735809771", "timeNano": "1735809771123456789"},
                "time": 1735809771123,
            },
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.bybit.test",
        transport=httpx.MockTransport(handler),
    )
    client = BybitClient(
        base_url="https://unused.test",
        timeout_seconds=1,
        instrument_page_size=1000,
        max_retries=1,
        retry_base_seconds=0.001,
        http_client=http_client,
    )

    assert await client.server_time_ms() == 1735809771123
    assert attempts == 2
    await http_client.aclose()
