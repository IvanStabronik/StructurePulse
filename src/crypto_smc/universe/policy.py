from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from crypto_smc.providers.models import Instrument, MarketAsset, MarketTicker


class ExclusionReason(StrEnum):
    MANUAL_DENYLIST = "manual_denylist"
    STABLECOIN = "stablecoin"
    WRAPPED_ASSET = "wrapped_asset"
    TOKENIZED_STOCK = "tokenized_stock"
    LEVERAGED_TOKEN = "leveraged_token"
    NO_BYBIT_CONTRACT = "no_bybit_contract"
    DUPLICATE_CONTRACT = "duplicate_contract"
    MISSING_TICKER = "missing_ticker"
    INSUFFICIENT_HISTORY = "insufficient_history"
    LOW_TURNOVER = "low_turnover"
    INVALID_SPREAD = "invalid_spread"
    SPREAD_TOO_WIDE = "spread_too_wide"
    CAPACITY_REACHED = "capacity_reached"


class UniversePolicyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    size: int
    min_turnover_24h_usdt: Decimal
    max_spread_bps: Decimal
    min_trading_history_days: int
    manual_denylist: frozenset[str] = frozenset()


class UniverseDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset: MarketAsset
    instrument_symbol: str | None
    exchange_turnover_24h_usdt: Decimal | None
    spread_bps: Decimal | None
    is_selected: bool
    exclusion_reason: ExclusionReason | None
    detail: str | None = None


STABLECOIN_IDS = frozenset(
    {
        "binance-usd",
        "dai",
        "ethena-usde",
        "first-digital-usd",
        "frax",
        "gemini-dollar",
        "paypal-usd",
        "pax-dollar",
        "stasis-eurs",
        "tether",
        "true-usd",
        "usdb",
        "usd-coin",
        "usdd",
        "usds",
    }
)
STABLECOIN_SYMBOLS = frozenset(
    {
        "BUSD",
        "DAI",
        "EURS",
        "FDUSD",
        "FRAX",
        "GUSD",
        "PYUSD",
        "TUSD",
        "USDC",
        "USDD",
        "USDE",
        "USDP",
        "USDS",
        "USDT",
    }
)
WRAPPED_SYMBOLS = frozenset({"CBBTC", "TBTC", "WBTC", "WETH", "WSTETH"})
CONTRACT_BASE_ALIASES = {
    "BONK": "1000BONK",
    "FLOKI": "1000FLOKI",
    "LUNC": "1000LUNC",
    "PEPE": "1000PEPE",
    "SATS": "1000SATS",
    "SHIB": "1000SHIB",
    "XEC": "1000XEC",
}


class UniversePolicy:
    def __init__(self, config: UniversePolicyConfig) -> None:
        self._config = config

    def evaluate(
        self,
        *,
        assets: list[MarketAsset],
        instruments: list[Instrument],
        tickers: dict[str, MarketTicker],
        now: datetime | None = None,
    ) -> list[UniverseDecision]:
        current_time = now or datetime.now(UTC)
        instruments_by_base = self._index_instruments(instruments)
        selected_symbols: set[str] = set()
        decisions: list[UniverseDecision] = []

        for asset in sorted(assets, key=lambda item: item.market_cap_rank):
            classification = self._classify(asset)
            if classification is not None:
                decisions.append(self._excluded(asset, classification))
                continue

            instrument = self._resolve_instrument(asset.symbol, instruments_by_base)
            if instrument is None:
                decisions.append(self._excluded(asset, ExclusionReason.NO_BYBIT_CONTRACT))
                continue
            if instrument.symbol in selected_symbols:
                decisions.append(
                    self._excluded(
                        asset,
                        ExclusionReason.DUPLICATE_CONTRACT,
                        instrument_symbol=instrument.symbol,
                    )
                )
                continue

            ticker = tickers.get(instrument.symbol)
            if ticker is None:
                decisions.append(
                    self._excluded(
                        asset,
                        ExclusionReason.MISSING_TICKER,
                        instrument_symbol=instrument.symbol,
                    )
                )
                continue

            minimum_launch_time = current_time - timedelta(
                days=self._config.min_trading_history_days
            )
            if instrument.launch_time > minimum_launch_time:
                decisions.append(
                    self._excluded(
                        asset,
                        ExclusionReason.INSUFFICIENT_HISTORY,
                        instrument_symbol=instrument.symbol,
                        ticker=ticker,
                        detail=instrument.launch_time.isoformat(),
                    )
                )
                continue
            if ticker.turnover_24h < self._config.min_turnover_24h_usdt:
                decisions.append(
                    self._excluded(
                        asset,
                        ExclusionReason.LOW_TURNOVER,
                        instrument_symbol=instrument.symbol,
                        ticker=ticker,
                    )
                )
                continue

            spread_bps = ticker.spread_bps
            if spread_bps is None or spread_bps < 0:
                decisions.append(
                    self._excluded(
                        asset,
                        ExclusionReason.INVALID_SPREAD,
                        instrument_symbol=instrument.symbol,
                        ticker=ticker,
                    )
                )
                continue
            if spread_bps > self._config.max_spread_bps:
                decisions.append(
                    self._excluded(
                        asset,
                        ExclusionReason.SPREAD_TOO_WIDE,
                        instrument_symbol=instrument.symbol,
                        ticker=ticker,
                    )
                )
                continue
            if len(selected_symbols) >= self._config.size:
                decisions.append(
                    self._excluded(
                        asset,
                        ExclusionReason.CAPACITY_REACHED,
                        instrument_symbol=instrument.symbol,
                        ticker=ticker,
                    )
                )
                continue

            selected_symbols.add(instrument.symbol)
            decisions.append(
                UniverseDecision(
                    asset=asset,
                    instrument_symbol=instrument.symbol,
                    exchange_turnover_24h_usdt=ticker.turnover_24h,
                    spread_bps=spread_bps,
                    is_selected=True,
                    exclusion_reason=None,
                )
            )

        return decisions

    def _classify(self, asset: MarketAsset) -> ExclusionReason | None:
        symbol = asset.symbol.upper()
        provider_id = asset.provider_id.lower()
        name = asset.name.lower()

        if symbol in self._config.manual_denylist or provider_id.upper() in (
            self._config.manual_denylist
        ):
            return ExclusionReason.MANUAL_DENYLIST
        if provider_id in STABLECOIN_IDS or symbol in STABLECOIN_SYMBOLS or "stablecoin" in name:
            return ExclusionReason.STABLECOIN
        if (
            provider_id.startswith("wrapped-")
            or name.startswith("wrapped ")
            or symbol in WRAPPED_SYMBOLS
        ):
            return ExclusionReason.WRAPPED_ASSET
        if "tokenized stock" in name or "stock token" in name:
            return ExclusionReason.TOKENIZED_STOCK
        if symbol.endswith(("3L", "3S", "5L", "5S", "BULL", "BEAR")):
            return ExclusionReason.LEVERAGED_TOKEN
        return None

    @staticmethod
    def _index_instruments(instruments: list[Instrument]) -> dict[str, list[Instrument]]:
        index: dict[str, list[Instrument]] = {}
        for instrument in instruments:
            index.setdefault(instrument.base_coin.upper(), []).append(instrument)
        return index

    @staticmethod
    def _resolve_instrument(
        asset_symbol: str,
        instruments_by_base: dict[str, list[Instrument]],
    ) -> Instrument | None:
        symbol = asset_symbol.upper()
        possible_bases = [symbol]
        alias = CONTRACT_BASE_ALIASES.get(symbol)
        if alias:
            possible_bases.append(alias)

        for base in possible_bases:
            candidates = instruments_by_base.get(base, [])
            if not candidates:
                continue
            expected_symbol = f"{base}USDT"
            return next(
                (instrument for instrument in candidates if instrument.symbol == expected_symbol),
                sorted(candidates, key=lambda item: item.symbol)[0],
            )
        return None

    @staticmethod
    def _excluded(
        asset: MarketAsset,
        reason: ExclusionReason,
        *,
        instrument_symbol: str | None = None,
        ticker: MarketTicker | None = None,
        detail: str | None = None,
    ) -> UniverseDecision:
        return UniverseDecision(
            asset=asset,
            instrument_symbol=instrument_symbol,
            exchange_turnover_24h_usdt=ticker.turnover_24h if ticker else None,
            spread_bps=ticker.spread_bps if ticker else None,
            is_selected=False,
            exclusion_reason=reason,
            detail=detail,
        )
