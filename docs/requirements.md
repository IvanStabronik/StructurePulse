# StructurePulse - Requirements v1.2

## 1. Purpose

StructurePulse monitors Bybit USDT linear perpetual futures, detects
deterministic Smart Money Concepts setups, tracks every signal virtually, and
can optionally execute live orders on Bybit.

The default safe mode is signal and virtual tracking only. Live execution is an
explicitly enabled mode controlled by environment variables and protected by
position, risk, leverage, slippage, daily-loss, and trade-count limits.

The system is an experimental trading assistant. It is not financial advice and
does not guarantee profit.

## 2. Current Trading Scope

- Exchange: Bybit.
- Market: USDT Linear Perpetual futures.
- Account type: Unified account.
- Position mode: One-Way.
- Directions: LONG and SHORT.
- Universe: top 30 eligible assets by market capitalization, after exclusions.
- Primary user timezone: Europe/Warsaw.
- Default Telegram language: Russian.
- Optional Telegram language: English.
- Strategy profile: configurable; current live testing can use a stricter or
  aggressive-test profile depending on `.env`.

The system can run market analysis and lifecycle tracking 24/7. Telegram
schedule settings control notification delivery, not market-data ingestion.

## 3. Trading Universe

The universe refreshes automatically.

1. Load leading cryptocurrencies by market capitalization from CoinGecko.
2. Exclude stablecoins, wrapped assets, tokenized stocks, leveraged tokens, and
   manual denylist entries.
3. Keep the first 30 eligible assets for live MVP testing.
4. Intersect assets with active Bybit USDT linear perpetual instruments.
5. Exclude instruments with insufficient liquidity, excessive spread, short
   trading history, or unhealthy data.

Bybit remains the source of tradable instruments, exchange constraints, market
data, and live execution data.

## 4. Timeframes and Candle Policy

The bot uses closed candles only:

- 4H: higher-timeframe context.
- 1H: market structure and liquidity.
- 15m: setup formation.
- 5m: entry confirmation.
- 1m: canonical data source and recovery base.

Higher timeframes are built from canonical closed 1m candles. Unfinished candles
must not confirm BOS, CHOCH, liquidity sweeps, or entries.

## 5. SMC Model v1

The strategy uses deterministic, testable definitions:

- Swing High/Low: local extremum with configurable left/right confirmation.
- BOS: confirmed close beyond a swing in the structure direction.
- CHOCH/MSS: confirmed close against prior structure.
- Liquidity Sweep: trade beyond liquidity followed by close back inside.
- Equal Highs/Lows: nearby extrema within ATR tolerance.
- FVG: three-candle imbalance above an ATR-relative minimum.
- Order Block: last opposite candle before displacement and structure break.
- Premium/Discount: position relative to the dealing-range midpoint.
- Displacement: impulse candle exceeding ATR and rolling body/range thresholds.

All thresholds are strategy-versioned.

## 6. Setup Rules

### LONG

1. Bullish or acceptably neutral 4H/1H context.
2. Price preferably in discount.
3. Sell-side liquidity sweep.
4. Bullish 15m CHOCH/BOS with displacement.
5. Valid bullish FVG or Order Block entry area.
6. Return to entry area.
7. Bullish 5m confirmation.
8. Minimum target structure that supports the configured reward/risk rules.

### SHORT

Mirrors LONG: bearish context, premium pricing, buy-side liquidity sweep,
bearish confirmation, and downside liquidity target.

## 7. Scoring

Candidates receive a 0-100 score:

- Higher-timeframe alignment.
- Liquidity sweep.
- Confirmed CHOCH/BOS.
- FVG or Order Block quality.
- Premium/Discount location.
- Volume and Open Interest confirmation.
- Funding and BTC market condition.

Signals below the configured threshold are suppressed. The current Telegram
default threshold is 70. Scores of 85+ are treated as strong.

## 8. Signal Lifecycle

Signals move through:

```text
preparing -> active -> entered -> tp1_reached -> terminal
```

Terminal states include:

- expired;
- invalidated;
- stopped;
- stopped_at_breakeven;
- tp2_completed;
- ambiguous;
- coverage_failed.

Rules:

- Only one active signal per instrument.
- Duplicate and correlated candidates are persisted as suppressed records.
- Every accepted signal is tracked virtually.
- Entry, TP, stop, and invalidation are resolved from ordered Bybit public
  trades when possible.
- If ordered trade coverage cannot prove event order, the system falls back to
  conservative handling and may mark the result ambiguous.
- TP1 closes 50% virtually.
- After TP1, the virtual stop moves to fee-adjusted breakeven.

## 9. Telegram Requirements

Telegram must:

- restrict access to configured allowed user IDs;
- send RU/EN localized messages;
- use PostgreSQL transactional outbox for durable delivery;
- support private commands:
  - `/signals`;
  - `/coin SYMBOL`;
  - `/settings`;
  - `/status`;
  - `/stats`;
  - `/language ru|en`;
  - `/threshold SCORE`;
  - `/schedule HH:MM HH:MM [TIMEZONE]`;
  - `/risk PERCENT BALANCE`;
  - `/pause`;
  - `/resume`.

Important distinction:

- `/settings` shows Telegram/user virtual settings such as reference balance
  and risk percent.
- Live execution risk is controlled by `.env` variables and is not changed by
  `/risk`.

## 10. Virtual Risk Model

Virtual signals use a reference account model:

- Reference balance default: 10,000 USDT.
- Risk default: 1%.
- Virtual risk amount is persisted in the signal.
- Virtual PnL includes estimated taker fees and directional funding estimates.

Virtual results are useful for strategy evaluation, but they can diverge from
real execution because market entries, slippage, exchange fills, fees, and stop
orders are different in live trading.

## 11. Live Execution Requirements

Live execution is optional and must be explicitly enabled:

```dotenv
EXECUTION_ENABLED=true
EXECUTION_MODE=auto
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
```

Supported mode now:

- `disabled`: never place orders.
- `auto`: place orders automatically when a virtual signal enters and passes
  live execution guards.

`approval` is reserved for future manual-confirmation flow.

Live execution must:

- use Bybit private API only when credentials are configured;
- set leverage before entry;
- open market entry orders only after current bid/ask passes slippage checks;
- set full-position stop after entry;
- reduce or close with reduce-only market orders;
- close remaining position on terminal virtual state;
- fetch Bybit closed PnL after live close and include it in Telegram;
- fail closed when data, balance, margin, or exchange state is not safe.

## 12. Current Live Risk Defaults

For the small live-test account, the local operational target is:

```dotenv
EXECUTION_RISK_USDT=20
EXECUTION_MIN_RISK_USDT=5
EXECUTION_MAX_EFFECTIVE_LEVERAGE=50
EXECUTION_MAX_OPEN_POSITIONS=1
EXECUTION_MAX_TRADES_PER_DAY=5
EXECUTION_MAX_DAILY_LOSS_USDT=60
EXECUTION_MAX_SLIPPAGE_BPS=20
```

Meaning:

- target live risk is 20 USDT;
- the bot may downsize risk to fit available margin, but not below 5 USDT;
- no more than one live position may be open at once;
- maximum five live attempts per UTC day;
- live daily guard stops new entries after configured loss;
- entries are skipped when current bid/ask is worse than the planned entry
  beyond the configured slippage limit or entry-zone boundary.

## 13. Live Entry Guard

Before live entry:

- LONG uses current ask.
- SHORT uses current bid.
- The price must remain inside the allowed entry guard.
- If price has moved too far, the bot records `live entry skipped` and does not
  send a Bybit order.

This intentionally reduces trade frequency to avoid entering after the virtual
signal has already moved.

## 14. Real PnL

On live close, the worker requests Bybit closed PnL:

- `Real PnL`;
- `Real entry`;
- `Real exit`.

Telegram live-close messages show these values when Bybit provides them.
Virtual PnL remains separate and must not be treated as the account result.

## 15. Persistence and Observability

PostgreSQL stores:

- instruments;
- universe snapshots;
- 1m and aggregated candles;
- strategy versions and analysis snapshots;
- signal candidates;
- signals and signal events;
- virtual trades;
- live executions;
- Telegram outbox and deliveries;
- observation windows and reports.

Health and metrics cover API, worker, PostgreSQL, market-data readiness,
outbox delivery, and operational warnings.

## 16. Validation and Readiness

Virtual strategy readiness still requires:

- at least 100 completed virtual signals;
- positive expectancy after estimated costs;
- Profit Factor above 1.3;
- drawdown below 15%;
- no single-symbol domination;
- no unresolved data-quality defects.

Live trading may be used for small-money operational testing, but a positive
small sample is not sufficient proof of a robust strategy.

## 17. Out of Scope For Current Version

- Multi-user product mode.
- Billing.
- Copy trading.
- Portfolio optimization.
- News analysis.
- Full web dashboard.
- Private WebSocket reconciliation.
- Persistent live-PnL analytics in database reports.

The next likely improvement is storing real live PnL as first-class database
data so observation reports can compare virtual and real outcomes directly.
