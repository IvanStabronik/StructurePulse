from collections.abc import Iterator

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
