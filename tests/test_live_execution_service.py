from decimal import Decimal
from typing import Any

from crypto_smc.db.repositories.execution import LiveSignalView
from crypto_smc.execution.service import LiveExecutionService, _quantity_for_risk
from crypto_smc.providers.bybit import BybitPosition, BybitPrivateAPIError


def signal_view(
    *,
    symbol: str = "BTCUSDT",
    direction: str = "long",
    signal_status: str = "entered",
    planned_entry: Decimal = Decimal("100"),
    quantity_step: Decimal = Decimal("0.001"),
    min_order_quantity: Decimal = Decimal("0.001"),
    max_market_order_quantity: Decimal = Decimal("1000"),
    min_notional_value: Decimal = Decimal("5"),
    live_id: int | None = None,
    live_status: str | None = None,
    live_remaining_qty: Decimal | None = None,
) -> LiveSignalView:
    return LiveSignalView(
        signal_id=1,
        symbol=symbol,
        direction=direction,
        signal_status=signal_status,
        planned_entry=planned_entry,
        stop_loss=Decimal("95"),
        current_stop=Decimal("95"),
        take_profit_1=Decimal("105"),
        take_profit_2=Decimal("110"),
        virtual_remaining_quantity=Decimal("1"),
        quantity_step=quantity_step,
        min_order_quantity=min_order_quantity,
        max_market_order_quantity=max_market_order_quantity,
        min_notional_value=min_notional_value,
        live_id=live_id,
        live_status=live_status,
        live_remaining_qty=live_remaining_qty,
    )


def test_quantity_for_risk_uses_stop_distance_and_rounds_down_to_exchange_step() -> None:
    qty = _quantity_for_risk(
        signal_view(planned_entry=Decimal("100"), quantity_step=Decimal("0.01")),
        Decimal("50"),
    )

    assert qty == Decimal("10")


def test_quantity_for_risk_rejects_too_small_order() -> None:
    qty = _quantity_for_risk(
        signal_view(
            planned_entry=Decimal("100"),
            min_notional_value=Decimal("2000"),
            quantity_step=Decimal("0.001"),
            min_order_quantity=Decimal("0.001"),
        ),
        Decimal("50"),
    )

    assert qty is None


class FakeCloseRepository:
    def __init__(self) -> None:
        self.closed = False
        self.failed = False
        self.closed_order_id: str | None = None
        self.close_claims = 0

    async def claim_close(self, *_: object, **__: object) -> Decimal:
        self.close_claims += 1
        return Decimal("0.35")

    async def mark_closed(self, *_: object, **kwargs: object) -> None:
        self.closed = True
        self.closed_order_id = str(kwargs["order_id"])

    async def mark_failed(self, *_: object, **__: object) -> None:
        self.failed = True


class FakeCloseClient:
    def __init__(self, *, position_size: Decimal, close_error: Exception | None = None) -> None:
        self.position_size = position_size
        self.close_error = close_error
        self.close_orders = 0

    async def get_linear_position(self, *, symbol: str) -> BybitPosition | None:
        if self.position_size <= 0:
            return None
        return BybitPosition(
            symbol=symbol,
            side="Sell",
            size=self.position_size,
            average_price=Decimal("72"),
        )

    async def place_market_order(self, **_: object) -> Any:
        self.close_orders += 1
        if self.close_error is not None:
            raise self.close_error
        return type("Order", (), {"order_id": "close-order"})()


async def test_close_marks_already_flat_position_closed_without_order() -> None:
    repository = FakeCloseRepository()
    client = FakeCloseClient(position_size=Decimal(0))
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("50"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("10"),
        poll_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._close(
        signal_view(
            planned_entry=Decimal("72"),
            quantity_step=Decimal("0.01"),
            signal_status="stopped_at_breakeven",
            live_id=1,
            live_status="tp1_reduced",
            live_remaining_qty=Decimal("0.35"),
        )
    )

    assert repository.closed is True
    assert repository.closed_order_id == ""
    assert repository.failed is False
    assert client.close_orders == 0


async def test_close_treats_bybit_zero_position_error_as_closed() -> None:
    repository = FakeCloseRepository()
    client = FakeCloseClient(
        position_size=Decimal("0.35"),
        close_error=BybitPrivateAPIError(
            "Bybit private error 110017: current position is zero, cannot fix reduce-only order qty"
        ),
    )
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("50"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("10"),
        poll_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._close(
        signal_view(
            planned_entry=Decimal("72"),
            quantity_step=Decimal("0.01"),
            signal_status="stopped_at_breakeven",
            live_id=1,
            live_status="tp1_reduced",
            live_remaining_qty=Decimal("0.35"),
        )
    )

    assert repository.closed is True
    assert repository.closed_order_id == ""
    assert repository.failed is False
    assert client.close_orders == 1
