from collections.abc import Sequence
from dataclasses import replace

from smc_core.models import Candle, Displacement, OrderBlock, StructureBreak


def detect_order_blocks(
    candles: Sequence[Candle],
    structure_breaks: Sequence[StructureBreak],
    displacements: Sequence[Displacement],
    *,
    search_lookback: int,
) -> tuple[OrderBlock, ...]:
    if search_lookback < 1:
        raise ValueError("search_lookback must be positive")

    displacement_by_index = {event.index: event for event in displacements}
    blocks: list[OrderBlock] = []
    used_candles: set[tuple[str, int]] = set()

    for break_event in structure_breaks:
        displacement = displacement_by_index.get(break_event.index)
        if displacement is None or displacement.direction != break_event.direction:
            continue

        start = max(0, break_event.index - search_lookback)
        opposite = "bearish" if break_event.direction == "bullish" else "bullish"
        candle_index = next(
            (
                index
                for index in range(break_event.index - 1, start - 1, -1)
                if candles[index].direction == opposite
            ),
            None,
        )
        if candle_index is None or (break_event.direction, candle_index) in used_candles:
            continue

        source = candles[candle_index]
        block = OrderBlock(
            direction=break_event.direction,
            candle_index=candle_index,
            created_index=break_event.index,
            created_at=break_event.time,
            lower_price=source.low_price,
            upper_price=source.high_price,
            break_event=break_event,
            status="open",
        )
        blocks.append(_resolve_order_block(block, candles))
        used_candles.add((break_event.direction, candle_index))
    return tuple(blocks)


def _resolve_order_block(block: OrderBlock, candles: Sequence[Candle]) -> OrderBlock:
    first_touch: int | None = None
    invalidated: int | None = None
    status = block.status

    for index in range(block.created_index + 1, len(candles)):
        candle = candles[index]
        if block.direction == "bullish":
            if candle.close_price < block.lower_price:
                invalidated = index
                status = "invalidated"
                break
            touched = candle.low_price <= block.upper_price
        else:
            if candle.close_price > block.upper_price:
                invalidated = index
                status = "invalidated"
                break
            touched = candle.high_price >= block.lower_price
        if touched and first_touch is None:
            first_touch = index
            status = "partially_filled"

    return replace(
        block,
        status=status,
        first_touch_index=first_touch,
        invalidated_index=invalidated,
    )
