import httpx
import pytest

from crypto_smc.api.main import create_app
from crypto_smc.config import Settings
from crypto_smc.providers.models import Instrument
from tests.test_bybit_client import instrument_payload


class FakeInstrumentProvider:
    def __init__(self) -> None:
        self.closed = False

    async def list_usdt_perpetual_instruments(self) -> list[Instrument]:
        payload = instrument_payload("BTCUSDT")
        return [
            Instrument(
                symbol=str(payload["symbol"]),
                base_coin=str(payload["baseCoin"]),
                quote_coin=str(payload["quoteCoin"]),
                settle_coin=str(payload["settleCoin"]),
                status="Trading",
                contract_type="LinearPerpetual",
                launch_time=Instrument.timestamp_ms_to_datetime(str(payload["launchTime"])),
                tick_size="0.1",  # type: ignore[arg-type]
                min_price="0.1",  # type: ignore[arg-type]
                max_price="1000000",  # type: ignore[arg-type]
                quantity_step="0.001",  # type: ignore[arg-type]
                min_order_quantity="0.001",  # type: ignore[arg-type]
                max_order_quantity="1000",  # type: ignore[arg-type]
                max_market_order_quantity="500",  # type: ignore[arg-type]
                min_notional_value="5",  # type: ignore[arg-type]
                min_leverage="1",  # type: ignore[arg-type]
                max_leverage="100",  # type: ignore[arg-type]
                leverage_step="0.01",  # type: ignore[arg-type]
                funding_interval_minutes=480,
            )
        ]

    async def close(self) -> None:
        self.closed = True


class FakeEngine:
    async def dispose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_debug_endpoint_is_disabled_by_default() -> None:
    app = create_app(
        Settings(app_env="test", debug_api_enabled=False),
        instrument_provider=FakeInstrumentProvider(),
        engine=FakeEngine(),  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/debug/instruments")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_debug_endpoint_lists_normalized_instruments() -> None:
    app = create_app(
        Settings(app_env="test", debug_api_enabled=True),
        instrument_provider=FakeInstrumentProvider(),
        engine=FakeEngine(),  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/debug/instruments")

    assert response.status_code == 200
    assert response.json() == {"count": 1, "symbols": ["BTCUSDT"]}
