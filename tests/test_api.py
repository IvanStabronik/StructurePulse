from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from crypto_smc.api.main import create_app
from crypto_smc.config import Settings
from crypto_smc.db.repositories.signals import SignalFilters, SignalView
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
                tick_size="0.1",
                min_price="0.1",
                max_price="1000000",
                quantity_step="0.001",
                min_order_quantity="0.001",
                max_order_quantity="1000",
                max_market_order_quantity="500",
                min_notional_value="5",
                min_leverage="1",
                max_leverage="100",
                leverage_step="0.01",
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


class FakeSignalRepository:
    def __init__(self) -> None:
        self.filters: SignalFilters | None = None

    async def list_signals(self, _: object, *, filters: SignalFilters) -> list[SignalView]:
        self.filters = filters
        return [
            SignalView(
                id=3,
                candidate_id=1,
                symbol="BTCUSDT",
                direction="long",
                status="preparing",
                suppression_reason=None,
                entry_lower=Decimal(100),
                entry_upper=Decimal(101),
                planned_entry=Decimal("100.5"),
                stop_loss=Decimal(98),
                take_profit_1=Decimal(104),
                take_profit_2=Decimal(110),
                quantity=Decimal(40),
                risk_amount=Decimal(100),
                expires_at=datetime(2026, 6, 15, 11, 30, tzinfo=UTC),
                created_at=datetime(2026, 6, 15, 10, tzinfo=UTC),
                trade_status="waiting_entry",
                realized_pnl=Decimal(0),
                r_multiple=Decimal(0),
                ambiguous=False,
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
async def test_readiness_rejects_outdated_database_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ready_database(_: object) -> bool:
        return True

    async def outdated_schema(_: object, *, required_revision: str) -> bool:
        assert required_revision == "0011"
        return False

    monkeypatch.setattr("crypto_smc.api.main.database_is_ready", ready_database)
    monkeypatch.setattr(
        "crypto_smc.api.main.database_schema_is_ready",
        outdated_schema,
    )
    app = create_app(
        Settings(app_env="test"),
        instrument_provider=FakeInstrumentProvider(),
        engine=FakeEngine(),  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "database_schema_outdated"


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


@pytest.mark.asyncio
async def test_debug_lifecycle_signals_exposes_tracking_state() -> None:
    repository = FakeSignalRepository()
    app = create_app(
        Settings(app_env="test", debug_api_enabled=True),
        instrument_provider=FakeInstrumentProvider(),
        engine=FakeEngine(),  # type: ignore[arg-type]
        signal_repository=repository,  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/debug/lifecycle-signals",
            params={"symbol": "btcusdt", "status": "preparing"},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["trade_status"] == "waiting_entry"
    assert repository.filters == SignalFilters(
        symbol="btcusdt",
        status="preparing",
        limit=100,
    )
