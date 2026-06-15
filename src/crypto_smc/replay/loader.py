import csv
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from crypto_smc.providers.models import Candle1m
from crypto_smc.replay.models import ReplayMarketRow

REQUIRED_COLUMNS = {
    "symbol",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
}


def load_replay_csv(path: Path) -> tuple[ReplayMarketRow, ...]:
    rows: list[ReplayMarketRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Replay CSV is missing columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                market_row = _parse_row(row)
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid replay CSV row {line_number}: {exc}") from exc
            rows.append(market_row)

    rows.sort(key=lambda item: (item.candle.open_time, item.candle.symbol))
    seen: set[tuple[str, datetime]] = set()
    for item in rows:
        key = (item.candle.symbol, item.candle.open_time)
        if key in seen:
            raise ValueError(f"Duplicate 1m candle: {key[0]} {key[1].isoformat()}")
        seen.add(key)
    return tuple(rows)


def _parse_row(row: dict[str, str | None]) -> ReplayMarketRow:
    symbol = _required_value(row, "symbol").upper()
    if not symbol:
        raise ValueError("symbol cannot be empty")
    open_time = _parse_timestamp(_required_value(row, "open_time"))
    candle = Candle1m(
        symbol=symbol,
        open_time=open_time,
        open_price=Decimal(_required_value(row, "open")),
        high_price=Decimal(_required_value(row, "high")),
        low_price=Decimal(_required_value(row, "low")),
        close_price=Decimal(_required_value(row, "close")),
        volume=Decimal(_required_value(row, "volume")),
        turnover=Decimal(_required_value(row, "turnover")),
    )
    _validate_finite(
        candle.open_price,
        candle.high_price,
        candle.low_price,
        candle.close_price,
        candle.volume,
        candle.turnover,
    )
    if (
        min(
            candle.open_price,
            candle.high_price,
            candle.low_price,
            candle.close_price,
        )
        <= 0
    ):
        raise ValueError("candle prices must be positive")
    if candle.volume < 0 or candle.turnover < 0:
        raise ValueError("volume and turnover cannot be negative")
    if candle.low_price > candle.high_price:
        raise ValueError("low exceeds high")
    if candle.low_price > min(candle.open_price, candle.close_price):
        raise ValueError("low exceeds candle body")
    if candle.high_price < max(candle.open_price, candle.close_price):
        raise ValueError("high is below candle body")
    market_row = ReplayMarketRow(
        candle=candle,
        open_interest=_optional_decimal(row.get("open_interest")),
        funding_rate=_optional_decimal(row.get("funding_rate")),
        spread_bps=_optional_decimal(row.get("spread_bps")),
        turnover_24h_usdt=_optional_decimal(row.get("turnover_24h_usdt")),
        instrument_max_leverage=_optional_decimal(row.get("instrument_max_leverage")),
        instrument_quantity_step=_optional_decimal(row.get("instrument_quantity_step")),
        instrument_min_notional=_optional_decimal(row.get("instrument_min_notional")),
    )
    optional_values = tuple(
        value
        for value in (
            market_row.open_interest,
            market_row.funding_rate,
            market_row.spread_bps,
            market_row.turnover_24h_usdt,
            market_row.instrument_max_leverage,
            market_row.instrument_quantity_step,
            market_row.instrument_min_notional,
        )
        if value is not None
    )
    _validate_finite(*optional_values)
    if market_row.instrument_max_leverage is not None and market_row.instrument_max_leverage < 1:
        raise ValueError("instrument_max_leverage must be at least one")
    if market_row.instrument_quantity_step is not None and market_row.instrument_quantity_step <= 0:
        raise ValueError("instrument_quantity_step must be positive")
    if market_row.instrument_min_notional is not None and market_row.instrument_min_notional < 0:
        raise ValueError("instrument_min_notional cannot be negative")
    return market_row


def _parse_timestamp(value: str) -> datetime:
    stripped = value.strip()
    if not stripped:
        raise ValueError("open_time cannot be empty")
    if stripped.isdigit():
        raw = int(stripped)
        seconds = raw / 1000 if raw >= 10_000_000_000 else raw
        return datetime.fromtimestamp(seconds, tz=UTC)
    parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("open_time must include a timezone")
    return parsed.astimezone(UTC)


def _optional_decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None and value.strip() else None


def _required_value(row: dict[str, str | None], name: str) -> str:
    value = row.get(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _validate_finite(*values: Decimal) -> None:
    if any(not value.is_finite() for value in values):
        raise ValueError("numeric values must be finite")
