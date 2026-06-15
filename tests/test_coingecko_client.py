import httpx
import pytest

from crypto_smc.providers.coingecko import CoinGeckoClient


@pytest.mark.asyncio
async def test_list_top_assets_normalizes_and_sorts() -> None:
    observed_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_params.update(request.url.params)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "ethereum",
                    "symbol": "eth",
                    "name": "Ethereum",
                    "current_price": 3000,
                    "market_cap": 350000000000,
                    "market_cap_rank": 2,
                    "total_volume": 15000000000,
                    "last_updated": "2026-06-14T10:00:00Z",
                },
                {
                    "id": "bitcoin",
                    "symbol": "btc",
                    "name": "Bitcoin",
                    "current_price": 100000,
                    "market_cap": 2000000000000,
                    "market_cap_rank": 1,
                    "total_volume": 30000000000,
                    "last_updated": "2026-06-14T10:00:00Z",
                },
                {
                    "id": "unranked",
                    "symbol": "none",
                    "name": "Unranked",
                    "current_price": None,
                    "market_cap": None,
                    "market_cap_rank": None,
                    "total_volume": None,
                },
            ],
        )

    http_client = httpx.AsyncClient(
        base_url="https://api.coingecko.test/api/v3",
        transport=httpx.MockTransport(handler),
    )
    client = CoinGeckoClient(
        base_url="https://unused.test",
        timeout_seconds=1,
        http_client=http_client,
    )

    assets = await client.list_top_assets(150)
    await http_client.aclose()

    assert [asset.symbol for asset in assets] == ["BTC", "ETH"]
    assert observed_params["order"] == "market_cap_desc"
    assert observed_params["per_page"] == "150"
    assert observed_params["sparkline"] == "false"
