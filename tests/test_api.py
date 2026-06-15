from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from crypto_smc.api.main import create_app
from crypto_smc.config import Settings
from crypto_smc.db.repositories.strategy import CandidateFilters, CandidateView
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


class FakeStrategyRepository:
    def __init__(self) -> None:
        self.filters: CandidateFilters | None = None

    async def list_candidates(self, _: object, *, filters: CandidateFilters) -> list[CandidateView]:
        self.filters = filters
        return [
            CandidateView(
                id=1,
                analysis_snapshot_id=2,
                symbol="BTCUSDT",
                direction="long",
                status="accepted",
                score=90,
                strength="strong",
                strategy_version="smc-v1.0.0",
                entry_lower=Decimal(100),
                entry_upper=Decimal(101),
                planned_entry=Decimal("100.5"),
                stop_loss=Decimal(98),
                take_profit_1=Decimal(104),
                take_profit_2=Decimal(110),
                gross_reward_to_risk=Decimal(4),
                net_reward_to_risk=Decimal("3.8"),
                risk_amount=Decimal(100),
                quantity=Decimal(40),
                notional=Decimal(4020),
                recommended_leverage=Decimal(20),
                estimated_margin=Decimal(201),
                estimated_loss_at_stop=Decimal(100),
                invalidation="close below 98",
                score_components=[],
                evidence=["bullish context"],
                warnings=[],
                suppression_reasons=[],
                analyzed_at=datetime(2026, 6, 15, 10, tzinfo=UTC),
                expires_at=datetime(2026, 6, 15, 11, 30, tzinfo=UTC),
                created_at=datetime(2026, 6, 15, 10, tzinfo=UTC),
            )
        ]


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


@pytest.mark.asyncio
async def test_debug_signals_forces_accepted_status_and_passes_filters() -> None:
    repository = FakeStrategyRepository()
    app = create_app(
        Settings(app_env="test", debug_api_enabled=True),
        instrument_provider=FakeInstrumentProvider(),
        engine=FakeEngine(),  # type: ignore[arg-type]
        strategy_repository=repository,  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/debug/signals",
            params={
                "symbol": "btcusdt",
                "direction": "long",
                "minimum_score": 85,
                "strategy_version": "smc-v1.0.0",
            },
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["symbol"] == "BTCUSDT"
    assert repository.filters == CandidateFilters(
        symbol="btcusdt",
        direction="long",
        status="accepted",
        minimum_score=85,
        strategy_version="smc-v1.0.0",
        created_from=None,
        created_to=None,
        limit=100,
    )
