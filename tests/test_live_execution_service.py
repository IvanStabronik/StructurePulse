from decimal import Decimal

from crypto_smc.db.repositories.execution import LiveSignalView
from crypto_smc.execution.service import _quantity_for_budget


def signal_view(
    *,
    planned_entry: Decimal = Decimal("100"),
    quantity_step: Decimal = Decimal("0.001"),
    min_order_quantity: Decimal = Decimal("0.001"),
    max_market_order_quantity: Decimal = Decimal("1000"),
    min_notional_value: Decimal = Decimal("5"),
) -> LiveSignalView:
    return LiveSignalView(
        signal_id=1,
        symbol="BTCUSDT",
        direction="long",
        signal_status="entered",
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
        live_id=None,
        live_status=None,
        live_remaining_qty=None,
    )


def test_quantity_for_budget_rounds_down_to_exchange_step() -> None:
    qty = _quantity_for_budget(
        signal_view(planned_entry=Decimal("333.33"), quantity_step=Decimal("0.01")),
        Decimal("50"),
    )

    assert qty == Decimal("0.15")


def test_quantity_for_budget_rejects_too_small_order() -> None:
    qty = _quantity_for_budget(
        signal_view(
            planned_entry=Decimal("100000"),
            quantity_step=Decimal("0.001"),
            min_order_quantity=Decimal("0.001"),
        ),
        Decimal("50"),
    )

    assert qty is None
