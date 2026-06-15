from dataclasses import dataclass
from datetime import datetime

from crypto_smc.providers.models import PublicTrade


@dataclass(frozen=True, slots=True)
class TradeCoverage:
    proven: bool
    reason: str | None
    trades: tuple[PublicTrade, ...]


def merge_trade_coverage(
    rest_trades: tuple[PublicTrade, ...],
    websocket_trades: tuple[PublicTrade, ...],
    *,
    coverage_start: datetime,
) -> TradeCoverage:
    if not rest_trades:
        return TradeCoverage(False, "rest_history_empty", ())
    if not websocket_trades:
        return TradeCoverage(False, "websocket_buffer_empty", ())
    symbols = {trade.symbol for trade in rest_trades + websocket_trades}
    if len(symbols) != 1:
        return TradeCoverage(False, "mixed_symbols", ())
    if rest_trades[0].executed_at > coverage_start:
        return TradeCoverage(False, "rest_history_too_shallow", ())
    rest_ids = {trade.trade_id for trade in rest_trades}
    websocket_ids = {trade.trade_id for trade in websocket_trades}
    if not rest_ids.intersection(websocket_ids):
        return TradeCoverage(False, "rest_websocket_overlap_missing", ())

    by_id = {
        trade.trade_id: trade
        for trade in rest_trades + websocket_trades
        if trade.executed_at >= coverage_start
    }
    ordered = tuple(
        sorted(
            by_id.values(),
            key=lambda trade: (trade.executed_at, trade.sequence, trade.trade_id),
        )
    )
    return TradeCoverage(True, None, ordered)
