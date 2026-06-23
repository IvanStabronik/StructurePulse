from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from crypto_smc.db.repositories.execution import LiveSignalView
from crypto_smc.execution.service import (
    LiveExecutionService,
    _entry_slippage_error,
    _quantity_for_risk,
    _risk_for_available_margin,
    _select_leverage,
)
from crypto_smc.providers.bybit import BybitPosition, BybitPrivateAPIError
from crypto_smc.providers.models import MarketTicker


def signal_view(
    *,
    symbol: str = "BTCUSDT",
    direction: str = "long",
    score: int = 90,
    signal_status: str = "entered",
    entry_lower: Decimal = Decimal("99"),
    entry_upper: Decimal = Decimal("101"),
    planned_entry: Decimal = Decimal("100"),
    stop_loss: Decimal = Decimal("95"),
    price_tick_size: Decimal = Decimal("0.01"),
    quantity_step: Decimal = Decimal("0.001"),
    min_order_quantity: Decimal = Decimal("0.001"),
    max_market_order_quantity: Decimal = Decimal("1000"),
    min_notional_value: Decimal = Decimal("5"),
    max_leverage: Decimal = Decimal("100"),
    live_id: int | None = None,
    live_status: str | None = None,
    live_remaining_qty: Decimal | None = None,
    live_entry_order_id: str | None = None,
    live_entry_order_link_id: str | None = None,
    live_entry_submitted_at: datetime | None = None,
) -> LiveSignalView:
    return LiveSignalView(
        signal_id=1,
        symbol=symbol,
        direction=direction,
        score=score,
        signal_status=signal_status,
        entry_lower=entry_lower,
        entry_upper=entry_upper,
        planned_entry=planned_entry,
        stop_loss=stop_loss,
        current_stop=stop_loss,
        take_profit_1=Decimal("105"),
        take_profit_2=Decimal("110"),
        virtual_remaining_quantity=Decimal("1"),
        price_tick_size=price_tick_size,
        quantity_step=quantity_step,
        min_order_quantity=min_order_quantity,
        max_market_order_quantity=max_market_order_quantity,
        min_notional_value=min_notional_value,
        max_leverage=max_leverage,
        live_id=live_id,
        live_status=live_status,
        live_remaining_qty=live_remaining_qty,
        live_entry_order_id=live_entry_order_id,
        live_entry_order_link_id=live_entry_order_link_id,
        live_entry_submitted_at=live_entry_submitted_at,
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


def test_select_leverage_raises_to_fit_available_margin() -> None:
    leverage = _select_leverage(
        notional=Decimal("18000"),
        configured_leverage=Decimal("20"),
        instrument_max_leverage=Decimal("100"),
        available_balance=Decimal("239.18205325"),
        max_effective_leverage=Decimal("100"),
    )

    assert leverage == Decimal("95")


def test_select_leverage_rejects_when_instrument_max_is_too_low() -> None:
    leverage = _select_leverage(
        notional=Decimal("21346.917"),
        configured_leverage=Decimal("20"),
        instrument_max_leverage=Decimal("50"),
        available_balance=Decimal("239.18205325"),
        max_effective_leverage=Decimal("50"),
    )

    assert leverage is None


def test_select_leverage_respects_effective_cap() -> None:
    leverage = _select_leverage(
        notional=Decimal("21346.917"),
        configured_leverage=Decimal("20"),
        instrument_max_leverage=Decimal("100"),
        available_balance=Decimal("239.18205325"),
        max_effective_leverage=Decimal("50"),
    )

    assert leverage is None


def test_risk_for_available_margin_downsizes_without_going_below_floor() -> None:
    risk = _risk_for_available_margin(
        signal_view(
            symbol="LABUSDT",
            planned_entry=Decimal("13.679"),
            stop_loss=Decimal("13.739782097674856170"),
            max_leverage=Decimal("20"),
        ),
        target_risk_usdt=Decimal("50"),
        min_risk_usdt=Decimal("15"),
        available_balance=Decimal("239.18"),
        max_effective_leverage=Decimal("50"),
    )

    assert risk == Decimal("17.00")


def test_risk_for_available_margin_rejects_below_floor() -> None:
    risk = _risk_for_available_margin(
        signal_view(
            symbol="XLMUSDT",
            planned_entry=Decimal("0.214185"),
            stop_loss=Decimal("0.214447347159921614"),
            max_leverage=Decimal("50"),
        ),
        target_risk_usdt=Decimal("50"),
        min_risk_usdt=Decimal("20"),
        available_balance=Decimal("239.18"),
        max_effective_leverage=Decimal("50"),
    )

    assert risk is None


def test_risk_for_available_margin_uses_effective_cap_not_instrument_max() -> None:
    risk = _risk_for_available_margin(
        signal_view(
            symbol="SOLUSDT",
            planned_entry=Decimal("70.545"),
            stop_loss=Decimal("70.379781854588682758"),
            max_leverage=Decimal("100"),
        ),
        target_risk_usdt=Decimal("30"),
        min_risk_usdt=Decimal("15"),
        available_balance=Decimal("239.18205325"),
        max_effective_leverage=Decimal("50"),
    )

    assert risk == Decimal("22.40")


def test_entry_slippage_error_rejects_long_above_allowed_price() -> None:
    error = _entry_slippage_error(
        signal_view(
            symbol="JUPUSDT",
            direction="long",
            entry_lower=Decimal("0.216740"),
            entry_upper=Decimal("0.217820"),
            planned_entry=Decimal("0.217280"),
        ),
        execution_price=Decimal("0.21805537"),
        max_slippage_bps=Decimal("20"),
    )

    assert error is not None
    assert "live entry skipped" in error


class FakeCloseRepository:
    def __init__(self) -> None:
        self.closed = False
        self.failed = False
        self.closed_order_id: str | None = None
        self.real_pnl: Decimal | None = None
        self.close_claims = 0

    async def claim_close(self, *_: object, **__: object) -> Decimal:
        self.close_claims += 1
        return Decimal("0.35")

    async def mark_closed(self, *_: object, **kwargs: object) -> None:
        self.closed = True
        self.closed_order_id = str(kwargs["order_id"])
        self.real_pnl = kwargs.get("real_pnl")  # type: ignore[assignment]

    async def mark_failed(self, *_: object, **__: object) -> None:
        self.failed = True


class FakeCloseClient:
    def __init__(self, *, position_size: Decimal, close_error: Exception | None = None) -> None:
        self.position_size = position_size
        self.close_error = close_error
        self.close_orders = 0
        self.last_order_qty = Decimal(0)

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
        self.last_order_qty = _["qty"]  # type: ignore[assignment]
        if self.close_error is not None:
            raise self.close_error
        return type("Order", (), {"order_id": "close-order"})()

    async def get_closed_pnl(self, **_: object) -> tuple[Any, ...]:
        return (
            type(
                "ClosedPnl",
                (),
                {
                    "order_id": "close-order",
                    "closed_pnl": Decimal("4.2"),
                    "average_entry_price": Decimal("14.14"),
                    "average_exit_price": Decimal("14.09"),
                },
            )(),
        )


class FakeEntryRepository:
    def __init__(self) -> None:
        self.failed_error: str | None = None
        self.rejected_error: str | None = None
        self.claims = 0
        self.rejections = 0
        self.rejected_notify: bool | None = None
        self.claimed_leverage: Decimal | None = None
        self.opened_qty: Decimal | None = None
        self.pending_order_id: str | None = None
        self.pending_price: Decimal | None = None
        self.cancelled_error: str | None = None
        self.closed_order_id: str | None = None
        self.closed_error: str | None = None
        self.real_pnl: Decimal | None = None
        self.tp1_claims = 0
        self.tp1_order_id: str | None = None
        self.tp1_remaining_qty: Decimal | None = None

    async def claim_entry(self, *_: object, **__: object) -> int:
        self.claims += 1
        self.claimed_leverage = __.get("leverage")  # type: ignore[assignment]
        return 7

    async def reject_entry(self, *_: object, **kwargs: object) -> None:
        self.rejections += 1
        self.rejected_error = str(kwargs["error"])
        self.rejected_notify = kwargs.get("notify")  # type: ignore[assignment]

    async def mark_failed(self, *_: object, **kwargs: object) -> None:
        self.failed_error = str(kwargs["error"])

    async def mark_entry_pending(self, *_: object, **kwargs: object) -> None:
        self.pending_order_id = str(kwargs["order_id"])
        self.pending_price = kwargs["limit_price"]  # type: ignore[assignment]

    async def mark_entry_cancelled(self, *_: object, **kwargs: object) -> None:
        self.cancelled_error = str(kwargs["error"])

    async def mark_entry_open(self, *_: object, **kwargs: object) -> None:
        self.opened_qty = kwargs["qty"]  # type: ignore[assignment]

    async def mark_closed(self, *_: object, **kwargs: object) -> None:
        self.closed_order_id = str(kwargs["order_id"])
        self.closed_error = kwargs.get("error")  # type: ignore[assignment]
        self.real_pnl = kwargs.get("real_pnl")  # type: ignore[assignment]

    async def claim_tp1(self, *_: object, **__: object) -> bool:
        self.tp1_claims += 1
        return True

    async def mark_tp1_reduced(self, *_: object, **kwargs: object) -> None:
        self.tp1_order_id = str(kwargs["order_id"])
        self.tp1_remaining_qty = kwargs["remaining_qty"]  # type: ignore[assignment]


class FakeEntryClient:
    def __init__(
        self,
        *,
        available_balance: Decimal,
        position_size: Decimal = Decimal(0),
        cancel_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.available_balance = available_balance
        self.cancel_error = cancel_error
        self.stop_error = stop_error
        self.orders = 0
        self.limit_orders = 0
        self.cancellations = 0
        self.leverage_updates = 0
        self.leverage_values: list[Decimal] = []
        self.stop_updates = 0
        self.last_order_qty = Decimal(0)
        self.last_limit_price = Decimal(0)
        self.position_size = position_size

    async def get_wallet_balance(self, **_: object) -> Any:
        return type(
            "Balance",
            (),
            {
                "total_available_balance": self.available_balance,
                "total_wallet_balance": self.available_balance,
            },
        )()

    async def set_linear_leverage(self, **kwargs: object) -> None:
        self.leverage_updates += 1
        self.leverage_values.append(kwargs["leverage"])  # type: ignore[arg-type]

    async def place_market_order(self, **_: object) -> Any:
        self.orders += 1
        self.last_order_qty = _["qty"]  # type: ignore[assignment]
        return type("Order", (), {"order_id": "entry-order"})()

    async def place_limit_order(self, **kwargs: object) -> Any:
        self.limit_orders += 1
        self.last_order_qty = kwargs["qty"]  # type: ignore[assignment]
        self.last_limit_price = kwargs["price"]  # type: ignore[assignment]
        return type("Order", (), {"order_id": "entry-limit-order"})()

    async def cancel_order(self, **_: object) -> None:
        self.cancellations += 1
        if self.cancel_error is not None:
            raise self.cancel_error

    async def get_linear_position(self, *, symbol: str) -> BybitPosition | None:
        if self.position_size <= 0:
            return None
        return BybitPosition(
            symbol=symbol,
            side="Buy",
            size=self.position_size,
            average_price=Decimal("70.545"),
        )

    async def set_full_position_stop(self, **_: object) -> None:
        self.stop_updates += 1
        if self.stop_error is not None:
            raise self.stop_error

    async def get_closed_pnl(self, **_: object) -> tuple[Any, ...]:
        return (
            type(
                "ClosedPnl",
                (),
                {
                    "order_id": "entry-order",
                    "closed_pnl": Decimal("-0.7"),
                    "average_entry_price": Decimal("0.6931"),
                    "average_exit_price": Decimal("0.6929"),
                },
            )(),
        )


class FakeTickerProvider:
    def __init__(self, *, ask_price: Decimal, bid_price: Decimal | None = None) -> None:
        self.ask_price = ask_price
        self.bid_price = bid_price if bid_price is not None else ask_price

    async def list_linear_tickers(self) -> dict[str, MarketTicker]:
        return {
            "JUPUSDT": MarketTicker(
                symbol="JUPUSDT",
                last_price=self.ask_price,
                mark_price=self.ask_price,
                bid_price=self.bid_price,
                ask_price=self.ask_price,
                turnover_24h=Decimal("1000000"),
                volume_24h=Decimal("1000000"),
                open_interest=Decimal("100000"),
                open_interest_value=Decimal("100000"),
                funding_rate=Decimal("0"),
            )
        }


async def test_enter_margin_failure_does_not_emergency_close_flat_position() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(available_balance=Decimal("100"))
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

    await service._enter(
        signal_view(
            symbol="HYPEUSDT",
            direction="long",
            planned_entry=Decimal("69.8155"),
            stop_loss=Decimal("69.4504"),
            quantity_step=Decimal("0.01"),
            max_leverage=Decimal("20"),
        )
    )

    assert repository.claims == 0
    assert repository.rejections == 1
    assert repository.rejected_error is not None
    assert "available balance 100 cannot support minimum live risk 20" in repository.rejected_error
    assert repository.failed_error is None
    assert client.leverage_updates == 0
    assert client.orders == 0


async def test_enter_raises_leverage_when_risk_fits_instrument_max() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(available_balance=Decimal("239.18205325"))
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("50"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        min_risk_usdt=Decimal("15"),
        max_effective_leverage=Decimal("50"),
        max_notional_to_wallet_ratio=Decimal("100"),
        repository=repository,  # type: ignore[arg-type]
    )

    await service._enter(
        signal_view(
            symbol="SOLUSDT",
            direction="long",
            planned_entry=Decimal("70.545"),
            stop_loss=Decimal("70.379781854588682758"),
            quantity_step=Decimal("0.1"),
            max_market_order_quantity=Decimal("12000"),
            max_leverage=Decimal("100"),
        )
    )

    assert repository.claims == 1
    assert repository.rejections == 0
    assert repository.claimed_leverage == Decimal("50")
    assert repository.pending_order_id == "entry-limit-order"
    assert repository.pending_price == Decimal("70.54")
    assert repository.opened_qty is None
    assert client.leverage_values == [Decimal("50")]
    assert client.orders == 0
    assert client.limit_orders == 1
    assert client.stop_updates == 0


async def test_enter_places_limit_order_even_when_market_moved_past_entry_zone() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(available_balance=Decimal("239.18205325"))
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("30"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        min_risk_usdt=Decimal("15"),
        max_effective_leverage=Decimal("50"),
        max_notional_to_wallet_ratio=Decimal("100"),
        max_slippage_bps=Decimal("20"),
        ticker_provider=FakeTickerProvider(ask_price=Decimal("0.21805537")),
        repository=repository,  # type: ignore[arg-type]
    )

    await service._enter(
        signal_view(
            symbol="JUPUSDT",
            direction="long",
            entry_lower=Decimal("0.216740"),
            entry_upper=Decimal("0.217820"),
            planned_entry=Decimal("0.217280"),
            stop_loss=Decimal("0.216452301107601981"),
            price_tick_size=Decimal("0.0001"),
            quantity_step=Decimal("1"),
            max_market_order_quantity=Decimal("1000000"),
            max_leverage=Decimal("100"),
        )
    )

    assert repository.claims == 1
    assert repository.rejections == 0
    assert repository.rejected_error is None
    assert repository.pending_order_id == "entry-limit-order"
    assert repository.pending_price == Decimal("0.2172")
    assert repository.failed_error is None
    assert client.leverage_updates == 1
    assert client.orders == 0
    assert client.limit_orders == 1
    assert client.stop_updates == 0


async def test_enter_rejects_low_score_for_live_execution() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(available_balance=Decimal("1000"))
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("20"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        min_signal_score=85,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._enter(signal_view(score=80))

    assert repository.rejections == 1
    assert repository.rejected_error is not None
    assert "below live minimum 85" in repository.rejected_error
    assert repository.rejected_notify is False
    assert client.limit_orders == 0


async def test_enter_rejects_notional_above_wallet_cap() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(available_balance=Decimal("1000"))
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("20"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        max_notional_to_wallet_ratio=Decimal("3"),
        repository=repository,  # type: ignore[arg-type]
    )

    await service._enter(signal_view(planned_entry=Decimal("100"), stop_loss=Decimal("99.8")))

    assert repository.rejections == 1
    assert repository.rejected_error is not None
    assert "exceeds 3x wallet cap" in repository.rejected_error
    assert client.limit_orders == 0


async def test_pending_limit_marks_open_when_bybit_position_appears() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(
        available_balance=Decimal("239.18205325"),
        position_size=Decimal("135.5"),
    )
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("50"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._handle(
        signal_view(
            symbol="SOLUSDT",
            signal_status="entered",
            live_id=7,
            live_status="entry_pending",
            live_remaining_qty=Decimal("135.5"),
            live_entry_order_id="entry-limit-order",
            live_entry_order_link_id="sp-1-entry",
        )
    )

    assert repository.opened_qty == Decimal("135.5")
    assert repository.cancelled_error is None
    assert client.stop_updates == 1
    assert client.cancellations == 0


async def test_pending_limit_emergency_closes_when_stop_is_rejected() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(
        available_balance=Decimal("239.18205325"),
        position_size=Decimal("83.8"),
        stop_error=BybitPrivateAPIError(
            "Bybit private error 10001: StopLoss should lower than LastPrice"
        ),
    )
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("20"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._handle(
        signal_view(
            symbol="SUIUSDT",
            signal_status="entered",
            live_id=7,
            live_status="entry_pending",
            live_remaining_qty=Decimal("83.8"),
            live_entry_order_id="entry-limit-order",
            live_entry_order_link_id="sp-1-entry",
            stop_loss=Decimal("0.6936"),
        )
    )

    assert repository.opened_qty is None
    assert repository.failed_error is None
    assert repository.closed_order_id == "entry-order"
    assert repository.real_pnl == Decimal("-0.7")
    assert repository.closed_error is not None
    assert "protective stop rejected" in repository.closed_error
    assert client.orders == 1
    assert client.last_order_qty == Decimal("83.8")
    assert client.stop_updates == 1


async def test_tp1_uses_actual_bybit_position_when_database_qty_is_stale() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(
        available_balance=Decimal("239.18205325"),
        position_size=Decimal("103.3"),
    )
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("20"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._take_profit_1(
        signal_view(
            symbol="SOLUSDT",
            direction="short",
            signal_status="tp1_reached",
            live_id=7,
            live_status="open",
            live_remaining_qty=Decimal("12.9"),
            quantity_step=Decimal("0.1"),
        )
    )

    assert repository.tp1_order_id == "entry-order"
    assert repository.tp1_remaining_qty == Decimal("51.7")
    assert client.last_order_qty == Decimal("51.6")
    assert client.stop_updates == 1


async def test_pending_limit_is_cancelled_when_signal_expires_before_fill() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(available_balance=Decimal("239.18205325"))
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("50"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._handle(
        signal_view(
            signal_status="expired",
            live_id=7,
            live_status="entry_pending",
            live_remaining_qty=Decimal("135.5"),
            live_entry_order_id="entry-limit-order",
            live_entry_order_link_id="sp-1-entry",
        )
    )

    assert repository.cancelled_error == "pending entry cancelled: signal status became expired"
    assert repository.opened_qty is None
    assert client.cancellations == 1


async def test_pending_limit_is_cancelled_after_timeout_even_if_signal_is_active() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(available_balance=Decimal("239.18205325"))
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("50"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        pending_entry_timeout_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._handle(
        signal_view(
            signal_status="active",
            live_id=7,
            live_status="entry_pending",
            live_remaining_qty=Decimal("135.5"),
            live_entry_order_id="entry-limit-order",
            live_entry_order_link_id="sp-1-entry",
            live_entry_submitted_at=datetime.now(UTC) - timedelta(seconds=2),
        )
    )

    assert repository.cancelled_error == "pending entry cancelled: exceeded 1s timeout"
    assert repository.opened_qty is None
    assert client.cancellations == 1


async def test_missing_pending_limit_order_is_treated_as_cancelled_when_flat() -> None:
    repository = FakeEntryRepository()
    client = FakeEntryClient(
        available_balance=Decimal("239.18205325"),
        cancel_error=BybitPrivateAPIError(
            "Bybit private error 110001: order not exists or too late to cancel"
        ),
    )
    service = LiveExecutionService(
        client=client,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        risk_usdt=Decimal("50"),
        leverage=Decimal("20"),
        max_open_positions=1,
        max_trades_per_day=2,
        max_daily_loss_usdt=Decimal("100"),
        poll_interval_seconds=1,
        repository=repository,  # type: ignore[arg-type]
    )

    await service._handle(
        signal_view(
            signal_status="expired",
            live_id=7,
            live_status="entry_pending",
            live_remaining_qty=Decimal("135.5"),
            live_entry_order_id="entry-limit-order",
            live_entry_order_link_id="sp-1-entry",
        )
    )

    assert repository.cancelled_error == (
        "pending entry cancel confirmed absent: signal status became expired"
    )
    assert repository.failed_error is None
    assert repository.opened_qty is None
    assert client.cancellations == 1


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
    assert repository.real_pnl == Decimal("4.2")
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
    assert repository.real_pnl == Decimal("4.2")
    assert repository.failed is False
    assert client.close_orders == 1


async def test_close_includes_real_pnl_for_submitted_close_order() -> None:
    repository = FakeCloseRepository()
    client = FakeCloseClient(position_size=Decimal("0.35"))
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
            signal_status="stopped",
            live_id=1,
            live_status="open",
            live_remaining_qty=Decimal("0.35"),
        )
    )

    assert repository.closed is True
    assert repository.closed_order_id == "close-order"
    assert repository.real_pnl == Decimal("4.2")
    assert client.close_orders == 1


async def test_close_uses_actual_bybit_position_when_database_qty_is_stale() -> None:
    repository = FakeCloseRepository()
    client = FakeCloseClient(position_size=Decimal("103.3"))
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
            symbol="SOLUSDT",
            direction="short",
            signal_status="stopped_at_breakeven",
            live_id=1,
            live_status="tp1_reduced",
            live_remaining_qty=Decimal("6.5"),
            quantity_step=Decimal("0.1"),
        )
    )

    assert repository.closed is True
    assert repository.closed_order_id == "close-order"
    assert client.close_orders == 1
    assert client.last_order_qty == Decimal("103.3")
