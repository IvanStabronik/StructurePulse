from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_smc.providers.models import Instrument, MarketAsset, MarketTicker
from crypto_smc.universe import UniversePolicy, UniversePolicyConfig
from crypto_smc.universe.policy import ExclusionReason

NOW = datetime(2026, 6, 14, tzinfo=UTC)


def asset(
    symbol: str,
    rank: int,
    *,
    provider_id: str | None = None,
    name: str | None = None,
) -> MarketAsset:
    return MarketAsset(
        provider_id=provider_id or symbol.lower(),
        symbol=symbol,
        name=name or symbol,
        market_cap_rank=rank,
        market_cap_usd=Decimal("1000000000"),
        total_volume_usd=Decimal("100000000"),
        current_price_usd=Decimal("100"),
        last_updated=NOW,
    )


def instrument(base: str, *, launched_days_ago: int = 365) -> Instrument:
    return Instrument(
        symbol=f"{base}USDT",
        base_coin=base,
        quote_coin="USDT",
        settle_coin="USDT",
        status="Trading",
        contract_type="LinearPerpetual",
        launch_time=NOW - timedelta(days=launched_days_ago),
        tick_size=Decimal("0.1"),
        min_price=Decimal("0.1"),
        max_price=Decimal("1000000"),
        quantity_step=Decimal("0.001"),
        min_order_quantity=Decimal("0.001"),
        max_order_quantity=Decimal("1000"),
        max_market_order_quantity=Decimal("500"),
        min_notional_value=Decimal("5"),
        min_leverage=Decimal("1"),
        max_leverage=Decimal("100"),
        leverage_step=Decimal("0.01"),
        funding_interval_minutes=480,
    )


def ticker(
    symbol: str,
    *,
    turnover: str = "100000000",
    bid: str = "99.95",
    ask: str = "100.05",
) -> MarketTicker:
    return MarketTicker(
        symbol=symbol,
        last_price=Decimal("100"),
        mark_price=Decimal("100"),
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        turnover_24h=Decimal(turnover),
        volume_24h=Decimal("1000000"),
        open_interest=Decimal("1000"),
        open_interest_value=Decimal("100000"),
        funding_rate=Decimal("0.0001"),
    )


def policy(*, size: int = 30, denylist: frozenset[str] = frozenset()) -> UniversePolicy:
    return UniversePolicy(
        UniversePolicyConfig(
            size=size,
            min_turnover_24h_usdt=Decimal("10000000"),
            max_spread_bps=Decimal("20"),
            min_trading_history_days=30,
            manual_denylist=denylist,
        )
    )


def test_policy_excludes_asset_categories_before_exchange_filters() -> None:
    assets = [
        asset("USDT", 1, provider_id="tether", name="Tether"),
        asset("WBTC", 2, provider_id="wrapped-bitcoin", name="Wrapped Bitcoin"),
        asset("TSLA", 3, name="Tesla Tokenized Stock"),
        asset("BTC3L", 4),
        asset("BLOCK", 5),
    ]

    decisions = policy(denylist=frozenset({"BLOCK"})).evaluate(
        assets=assets,
        instruments=[],
        tickers={},
        now=NOW,
    )

    assert [decision.exclusion_reason for decision in decisions] == [
        ExclusionReason.STABLECOIN,
        ExclusionReason.WRAPPED_ASSET,
        ExclusionReason.TOKENIZED_STOCK,
        ExclusionReason.LEVERAGED_TOKEN,
        ExclusionReason.MANUAL_DENYLIST,
    ]


def test_policy_applies_exchange_quality_filters_and_capacity() -> None:
    assets = [
        asset("BTC", 1),
        asset("ETH", 2),
        asset("SOL", 3),
        asset("XRP", 4),
        asset("ADA", 5),
    ]
    instruments = [
        instrument("BTC"),
        instrument("ETH", launched_days_ago=5),
        instrument("SOL"),
        instrument("XRP"),
        instrument("ADA"),
    ]
    tickers = {
        "BTCUSDT": ticker("BTCUSDT"),
        "ETHUSDT": ticker("ETHUSDT"),
        "SOLUSDT": ticker("SOLUSDT", turnover="1000"),
        "XRPUSDT": ticker("XRPUSDT", bid="99", ask="101"),
        "ADAUSDT": ticker("ADAUSDT"),
    }

    decisions = policy(size=1).evaluate(
        assets=assets,
        instruments=instruments,
        tickers=tickers,
        now=NOW,
    )

    assert decisions[0].is_selected
    assert [decision.exclusion_reason for decision in decisions[1:]] == [
        ExclusionReason.INSUFFICIENT_HISTORY,
        ExclusionReason.LOW_TURNOVER,
        ExclusionReason.SPREAD_TOO_WIDE,
        ExclusionReason.CAPACITY_REACHED,
    ]


def test_policy_uses_scaled_contract_alias() -> None:
    pepe = asset("PEPE", 1)
    scaled = instrument("1000PEPE")

    decisions = policy().evaluate(
        assets=[pepe],
        instruments=[scaled],
        tickers={scaled.symbol: ticker(scaled.symbol)},
        now=NOW,
    )

    assert decisions[0].is_selected
    assert decisions[0].instrument_symbol == "1000PEPEUSDT"
