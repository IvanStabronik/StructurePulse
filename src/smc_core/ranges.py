from collections.abc import Sequence
from decimal import Decimal

from smc_core.models import DealingRange, PriceLocation, Swing


def active_dealing_range(swings: Sequence[Swing]) -> DealingRange | None:
    high = max((swing for swing in swings if swing.kind == "high"), key=_recency, default=None)
    low = max((swing for swing in swings if swing.kind == "low"), key=_recency, default=None)
    if high is None or low is None or low.price >= high.price:
        return None

    return DealingRange(
        low_swing=low,
        high_swing=high,
        low_price=low.price,
        high_price=high.price,
        midpoint=(low.price + high.price) / Decimal(2),
    )


def classify_price(
    price: Decimal,
    dealing_range: DealingRange,
) -> PriceLocation:
    if price < dealing_range.midpoint:
        return "discount"
    if price > dealing_range.midpoint:
        return "premium"
    return "equilibrium"


def _recency(swing: Swing) -> tuple[int, int]:
    return swing.confirmation_index, swing.index
