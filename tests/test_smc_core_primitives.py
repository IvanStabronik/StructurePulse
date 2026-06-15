from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from smc_core import (
    Candle,
    SMCConfig,
    StructureBreak,
    Swing,
    active_dealing_range,
    analyze,
    average_true_range,
    classify_price,
    detect_displacements,
    detect_equal_levels,
    detect_fair_value_gaps,
    detect_liquidity_sweeps,
    detect_order_blocks,
    detect_structure_breaks,
    detect_swings,
    rolling_mean,
    true_ranges,
)
from smc_core.models import Displacement

START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(
    index: int,
    open_price: str,
    high_price: str,
    low_price: str,
    close_price: str,
    *,
    timeframe: str = "5m",
) -> Candle:
    open_time = START + timedelta(minutes=index * 5)
    return Candle(
        symbol="BTCUSDT",
        timeframe=timeframe,  # type: ignore[arg-type]
        open_time=open_time,
        close_time=open_time + timedelta(minutes=5),
        open_price=Decimal(open_price),
        high_price=Decimal(high_price),
        low_price=Decimal(low_price),
        close_price=Decimal(close_price),
        volume=Decimal(10),
    )


def swing(
    kind: str,
    index: int,
    confirmation_index: int,
    price: str,
) -> Swing:
    return Swing(
        kind=kind,  # type: ignore[arg-type]
        index=index,
        confirmation_index=confirmation_index,
        time=START + timedelta(minutes=index * 5),
        price=Decimal(price),
    )


def test_rolling_statistics_and_wilder_atr() -> None:
    candles = (
        candle(0, "9", "10", "8", "9"),
        candle(1, "11", "12", "10", "11"),
        candle(2, "11", "15", "9", "14"),
    )

    assert rolling_mean((Decimal(1), Decimal(3), Decimal(5)), 2) == (
        None,
        Decimal(2),
        Decimal(4),
    )
    assert true_ranges(candles) == (Decimal(2), Decimal(3), Decimal(6))
    assert average_true_range(candles, 2) == (
        None,
        Decimal("2.5"),
        Decimal("4.25"),
    )


def test_confirmed_swings_are_strict_and_do_not_look_ahead() -> None:
    candles = (
        candle(0, "10", "11", "9", "10"),
        candle(1, "10", "15", "10", "14"),
        candle(2, "14", "14", "8", "9"),
        candle(3, "9", "13", "9", "12"),
    )

    result = detect_swings(candles, lookback=1)

    assert [(item.kind, item.index, item.confirmation_index) for item in result] == [
        ("high", 1, 2),
        ("low", 2, 3),
    ]
    assert detect_swings(candles[:2], lookback=1) == ()


def test_equal_neighbour_high_is_not_a_strict_swing() -> None:
    candles = (
        candle(0, "10", "11", "9", "10"),
        candle(1, "10", "15", "9", "14"),
        candle(2, "14", "15", "10", "11"),
    )

    assert detect_swings(candles, lookback=1) == ()


def test_bos_then_mirrored_choch_require_close_beyond_level() -> None:
    candles = (
        candle(0, "100", "102", "99", "101"),
        candle(1, "101", "105", "100", "104"),
        candle(2, "104", "104", "98", "99"),
        candle(3, "99", "103", "97", "102"),
        candle(4, "102", "107", "101", "106"),
        candle(5, "106", "107", "100", "101"),
        candle(6, "101", "104", "100", "103"),
        candle(7, "103", "104", "97", "99"),
    )
    swings = (
        swing("high", 1, 2, "105"),
        swing("low", 5, 6, "100"),
    )

    result = detect_structure_breaks(candles, swings)

    assert [(event.kind, event.direction, event.index) for event in result] == [
        ("bos", "bullish", 4),
        ("choch", "bearish", 7),
    ]
    boundary = list(candles)
    boundary[4] = candle(4, "102", "106", "101", "105")
    assert detect_structure_breaks(boundary[:5], swings[:1]) == ()


@pytest.mark.parametrize(
    ("kind", "price", "event_candle", "expected_direction"),
    [
        ("high", "105", candle(4, "102", "107", "101", "104"), "bearish"),
        ("low", "95", candle(4, "98", "99", "93", "96"), "bullish"),
    ],
)
def test_liquidity_sweep_is_mirrored(
    kind: str,
    price: str,
    event_candle: Candle,
    expected_direction: str,
) -> None:
    candles = (
        candle(0, "100", "102", "98", "101"),
        candle(1, "101", "103", "99", "102"),
        candle(2, "102", "104", "98", "100"),
        candle(3, "100", "103", "97", "101"),
        event_candle,
    )

    result = detect_liquidity_sweeps(candles, (swing(kind, 1, 2, price),))

    assert len(result) == 1
    assert result[0].direction == expected_direction


def test_sweep_requires_strict_close_back_inside() -> None:
    candles = (
        candle(0, "100", "102", "98", "101"),
        candle(1, "101", "103", "99", "102"),
        candle(2, "102", "104", "98", "100"),
        candle(3, "100", "106", "99", "105"),
    )

    assert detect_liquidity_sweeps(candles, (swing("high", 1, 2, "105"),)) == ()


def test_equal_highs_and_lows_use_atr_tolerance_boundary() -> None:
    swings = (
        swing("high", 1, 2, "105"),
        swing("low", 2, 3, "95"),
        swing("high", 5, 6, "105.5"),
        swing("low", 6, 7, "94.5"),
    )
    atr_values = (None, None, None, None, None, None, Decimal(5), Decimal(5))

    result = detect_equal_levels(
        swings,
        atr_values,
        tolerance_ratio=Decimal("0.10"),
        max_separation=10,
    )

    assert [(level.kind, level.price) for level in result] == [
        ("high", Decimal("105.25")),
        ("low", Decimal("94.75")),
    ]


@pytest.mark.parametrize(
    ("candles", "direction"),
    [
        (
            (
                candle(0, "99", "101", "98", "100"),
                candle(1, "100", "102", "99", "101"),
                candle(2, "104", "105", "103", "104"),
                candle(3, "104", "105", "102", "103"),
                candle(4, "103", "104", "100", "101"),
            ),
            "bullish",
        ),
        (
            (
                candle(0, "104", "105", "103", "104"),
                candle(1, "103", "104", "102", "103"),
                candle(2, "100", "101", "99", "100"),
                candle(3, "100", "102", "99", "101"),
                candle(4, "101", "104", "100", "103"),
            ),
            "bearish",
        ),
    ],
)
def test_fvg_lifecycle_is_mirrored(
    candles: tuple[Candle, ...],
    direction: str,
) -> None:
    result = detect_fair_value_gaps(
        candles,
        (Decimal(10),) * len(candles),
        min_atr_ratio=Decimal("0.10"),
    )

    matching = [gap for gap in result if gap.direction == direction]
    assert matching
    assert matching[0].status == "filled"
    assert matching[0].first_touch_index == 3
    assert matching[0].resolved_index == 4


def test_fvg_size_boundary_is_inclusive() -> None:
    candles = (
        candle(0, "99", "101", "98", "100"),
        candle(1, "100", "102", "99", "101"),
        candle(2, "103", "104", "102", "103"),
    )

    result = detect_fair_value_gaps(
        candles,
        (Decimal(10),) * 3,
        min_atr_ratio=Decimal("0.10"),
    )

    assert len(result) == 1
    assert result[0].size == Decimal(1)


@pytest.mark.parametrize(
    ("event", "direction"),
    [
        (candle(2, "100", "108", "99", "107"), "bullish"),
        (candle(2, "107", "108", "99", "100"), "bearish"),
    ],
)
def test_displacement_is_mirrored(event: Candle, direction: str) -> None:
    candles = (
        candle(0, "100", "102", "100", "101"),
        candle(1, "101", "103", "101", "102"),
        event,
    )

    result = detect_displacements(
        candles,
        (Decimal(2),) * 3,
        range_average_period=2,
        body_atr_ratio=Decimal(1),
        range_average_ratio=Decimal("1.5"),
        close_fraction=Decimal("0.70"),
    )

    assert [(item.direction, item.index) for item in result] == [(direction, 2)]


def test_order_block_requires_matching_displacement_and_tracks_invalidation() -> None:
    candles = (
        candle(0, "100", "102", "99", "101"),
        candle(1, "101", "103", "100", "102"),
        candle(2, "102", "103", "98", "99"),
        candle(3, "99", "104", "99", "103"),
        candle(4, "103", "110", "102", "109"),
        candle(5, "109", "110", "101", "104"),
        candle(6, "104", "105", "97", "97.5"),
    )
    broken_swing = swing("high", 1, 2, "105")
    break_event = StructureBreak(
        kind="bos",
        direction="bullish",
        index=4,
        time=candles[4].close_time,
        close_price=Decimal(109),
        broken_swing=broken_swing,
        prior_trend=None,
    )
    displacement = Displacement(
        direction="bullish",
        index=4,
        time=candles[4].close_time,
        body_size=Decimal(6),
        range_size=Decimal(8),
        atr=Decimal(3),
        average_range=Decimal(4),
    )

    result = detect_order_blocks(
        candles,
        (break_event,),
        (displacement,),
        search_lookback=5,
    )

    assert len(result) == 1
    assert result[0].candle_index == 2
    assert result[0].first_touch_index == 5
    assert result[0].status == "invalidated"
    assert result[0].invalidated_index == 6
    assert detect_order_blocks(candles, (break_event,), (), search_lookback=5) == ()


def test_dealing_range_classifies_discount_equilibrium_and_premium() -> None:
    result = active_dealing_range(
        (
            swing("low", 2, 3, "90"),
            swing("high", 4, 5, "110"),
        )
    )

    assert result is not None
    assert result.midpoint == Decimal(100)
    assert classify_price(Decimal(99), result) == "discount"
    assert classify_price(Decimal(100), result) == "equilibrium"
    assert classify_price(Decimal(101), result) == "premium"


def test_full_analysis_is_deterministic_and_timeframe_configured() -> None:
    candles = (
        candle(0, "100", "102", "99", "101", timeframe="1h"),
        candle(1, "101", "106", "100", "105", timeframe="1h"),
        candle(2, "105", "105", "97", "98", timeframe="1h"),
        candle(3, "98", "104", "98", "103", timeframe="1h"),
        candle(4, "103", "110", "102", "109", timeframe="1h"),
        candle(5, "109", "110", "101", "102", timeframe="1h"),
    )
    config = SMCConfig(
        atr_period=2,
        range_average_period=2,
        displacement_body_atr_ratio=Decimal("0.5"),
        displacement_range_average_ratio=Decimal("0.8"),
    )

    first = analyze(candles, config)
    second = analyze(candles, config)

    assert first == second
    assert first.symbol == "BTCUSDT"
    assert first.timeframe == "1h"
    assert first.swings
