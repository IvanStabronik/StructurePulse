# Crypto SMC Signal Bot - Requirements v1.1

## 1. Purpose

The system monitors the cryptocurrency futures market, detects Smart Money
Concepts (SMC) setups, and sends informational LONG and SHORT signals to
Telegram.

The first release does not place or manage real orders. The architecture must
allow an execution module to be added later without rewriting the analysis
engine.

## 2. Trading scope

- Exchange: Bybit.
- Market: USDT Linear Perpetual futures.
- Position mode for a future execution module: One-Way.
- Directions: LONG and SHORT.
- Trading days: every day, including weekends.
- User time zone: Europe/Warsaw.
- Signal notification window: 07:00 through 20:00 local time.
- Default language: Russian.
- Optional language: English.

Market analysis and data collection may run outside the notification window.
New entry signals must only be delivered inside the notification window.
Existing virtual positions may be updated at any time.

## 3. Trading universe

The universe is refreshed once per day.

1. Load leading cryptocurrencies by market capitalization from CoinGecko.
2. Exclude stablecoins, wrapped assets, tokenized stocks, leveraged tokens,
   and assets on a configurable denylist.
3. Keep the first 30 eligible assets for the MVP. The universe size is
   configurable up to an initial supported maximum of 60 assets.
4. Intersect them with active Bybit USDT Linear Perpetual instruments.
5. Exclude instruments that fail liquidity, spread, trading-history, or data
   quality requirements.

CoinGecko is the initial capitalization-ranking provider because it exposes
the required ranking data and can later be replaced behind a provider
interface. Bybit remains the source of tradable instruments and all trading
market data.

## 4. Timeframes and candle policy

- 4H: higher-timeframe trend and dealing range.
- 1H: market structure and important liquidity.
- 15m: setup formation.
- 5m: mandatory entry confirmation.

All structural decisions are based on closed candles. An unfinished candle
must never confirm BOS, CHOCH, a liquidity sweep, or an entry.

## 5. SMC model v1

The implementation must use deterministic definitions that can be tested:

- Swing High/Low: confirmed local extremum with three closed candles on each
  side by default. The lookback must be configurable independently for every
  timeframe. Initial defaults are 1 candle for 4H and 1H, and 3 candles for
  15m and 5m.
- BOS: a candle closes beyond a confirmed swing in the direction of the
  current structure.
- CHOCH/MSS: the first confirmed structural close against the prior structure,
  normally following a liquidity sweep.
- Liquidity Sweep: price trades beyond a confirmed swing or equal highs/lows
  and closes back inside the level.
- Equal Highs/Lows: nearby confirmed extrema within an ATR-based tolerance.
- FVG: a three-candle imbalance whose minimum size is defined relative to ATR.
- Order Block: the last opposite candle before displacement that produces a
  confirmed structural break.
- Premium/Discount: position relative to the 50% midpoint of the active
  dealing range.
- Displacement: an impulse candle whose body and range exceed configurable
  ATR and rolling-average thresholds.

All thresholds must be configurable and versioned with the strategy.

Changing a structural parameter creates a new strategy version. Signals and
virtual trades must retain the exact strategy version and parameter snapshot
that produced them.

## 6. Setup rules

### LONG

1. Bullish or acceptably neutral 4H/1H context.
2. Price is preferably in discount.
3. Sell-side liquidity is swept.
4. A bullish 15m CHOCH/BOS occurs with displacement.
5. A valid bullish FVG or Order Block provides an entry area.
6. Price returns to the entry area.
7. Bullish 5m structure confirms the entry.
8. A logical liquidity target provides at least 1:3 reward-to-risk.

### SHORT

The LONG rules are mirrored: bearish context, premium pricing, buy-side
liquidity sweep, bearish structure confirmation, and a downside liquidity
target.

## 7. Signal score

Each candidate receives a score from 0 to 100:

- Higher-timeframe alignment: 20.
- Liquidity sweep: 20.
- Confirmed CHOCH/BOS: 20.
- FVG and/or Order Block quality: 15.
- Premium/Discount location: 10.
- Volume and Open Interest confirmation: 10.
- Funding and BTC market condition: 5.

Only signals scoring at least 70 are delivered. Scores of 85 or higher are
marked as strong. Component weights and the threshold must be configurable.

## 8. Signal contents

Each signal must include:

- Symbol and direction.
- Score and strength label.
- Entry zone.
- Stop Loss and invalidation condition.
- Take Profit 1 and Take Profit 2.
- Expected reward-to-risk.
- Suggested position size for the configured account.
- Short explanation of the confirming factors.
- Creation and expiration times in Europe/Warsaw.
- Strategy version.

The initial signal lifetime is 90 minutes. The signal expires sooner if its
invalidation condition is met.

Initial virtual position management:

- TP1 closes 50% of the virtual position.
- TP2 closes the remaining 50%.
- After TP1, the Stop Loss for the remainder moves to fee-adjusted breakeven.
- TP2 must provide at least 1:3 reward-to-risk from the planned entry.
- Target allocation and post-TP1 Stop Loss policy are configurable and stored
  with the strategy version.

## 9. Risk model

- Reference account balance: 10,000 USDT.
- Risk per signal: 1%, initially 100 USDT.
- Minimum reward-to-risk: 1:3 after estimated fees.
- Maximum displayed leverage: 20x.
- Preferred margin mode for future execution: isolated.
- Position size is calculated from entry-to-stop distance and risk amount.
- Leverage must not be used to increase the defined monetary risk.
- A warning is required when 20x creates an unsafe liquidation buffer relative
  to the Stop Loss.

The bot may recommend less than 20x when volatility, exchange limits, or the
liquidation buffer make 20x unsuitable.

## 10. Market filters

Initial filters:

- Minimum 24-hour turnover.
- Maximum bid/ask spread.
- Minimum trading history.
- ATR-based minimum and maximum volatility.
- Funding-rate penalty for crowded positioning.
- Volume and Open Interest confirmation.
- Data completeness and freshness checks.

An abnormal BTC movement does not completely disable signals. It lowers the
score where appropriate and adds a visible warning.

## 11. Signal lifecycle

- Only one active signal is allowed per instrument.
- Duplicate same-direction signals are suppressed.
- A new signal is allowed after the previous signal is expired, invalidated,
  or completed, subject to a configurable cooldown.
- Every signal is tracked as a virtual trade whether or not the user trades it.
- Entry is considered filled when Bybit market data first touches the entry
  zone before expiration.
- Active virtual trades are resolved from the ordered Bybit public trade
  WebSocket stream, not from 5m or 15m candle high/low values. The stored
  exchange timestamp and stream order determine whether entry, Stop Loss, or
  Take Profit was touched first.
- If ordered trade events are unavailable because of an outage, the system
  backfills 1m candles. If Stop Loss and Take Profit are both touched inside
  the same 1m candle and their order cannot be proven, the conservative
  stop-first result is used and the trade is marked `ambiguous`.
- Outcomes include: expired, invalidated before entry, stopped, stopped after
  TP1, TP2, ambiguous, or manually closed in a future release.
- Fees and funding estimates are included in performance calculations.

## 12. Market data transport and recovery

- Bybit WebSockets are the primary live source for public trades, tickers, and
  closed kline updates.
- Bybit REST is used for instrument discovery, initial historical backfill,
  recovery of gaps, periodic reconciliation, and market data unavailable from
  a required WebSocket stream.
- CoinGecko is queried only during the daily universe refresh. Its last valid
  result is cached, and a provider failure must not remove the current trading
  universe.
- WebSocket connections must reconnect with exponential backoff and jitter.
- Every stored market-data series has a durable last-confirmed checkpoint.
- On startup or reconnect, the application enters a warming state and cannot
  generate new signals until required gaps have been reconciled.
- Recovery subscribes to and buffers live WebSocket events, backfills the gap
  through REST, merges both sources by exchange timestamp and event identity,
  removes duplicates, and then switches the instrument to ready state.
- Missing or conflicting data keeps the affected instrument unavailable for
  new signals while other healthy instruments continue operating.
- Closed candles built from live data are periodically compared with Bybit
  REST candles. Material differences generate an audit event and trigger
  repair before further signals are allowed for that instrument.

## 13. Global signal protection

The system must prevent correlated market events from flooding Telegram or
creating a misleading cluster of equivalent signals.

- Default portfolio limit: no more than 5 new entry signals in any rolling
  60-minute window.
- Default burst limit: no more than 2 new entry signals in any rolling
  5-minute window.
- Excess candidates remain stored as suppressed signals with their score and
  suppression reason. They are not sent later after their setup becomes stale.
- If a closed BTCUSDT 5m candle has an absolute return of at least 2%, or its
  true range is at least 2.5 times ATR(14), the global circuit breaker pauses
  new entry signals for 30 minutes.
- The BTC circuit breaker sends one warning, continues market analysis, and
  continues tracking existing virtual trades.
- The pause may end only after its minimum duration and after BTC data is
  healthy. Manual `/pause` always takes precedence.
- Limits, thresholds, and pause duration are configurable.

## 14. Telegram interface

The initial bot is single-user. Access is restricted to an allowed Telegram
user ID.

Required commands:

- `/signals` - active signals.
- `/coin SYMBOL` - latest analysis for one instrument.
- `/settings` - current settings.
- `/language` - Russian or English.
- `/threshold` - minimum signal score.
- `/schedule` - notification window.
- `/risk` - risk percentage and reference balance.
- `/pause` and `/resume` - notification control.
- `/stats` - virtual performance.
- `/status` - service and data-source status.

## 15. Persistence and observability

- Database: PostgreSQL from the first release.
- Store instruments, candles needed for analysis, detected structures, signal
  score components, signals, suppressed candidates, virtual trades, market
  data checkpoints, recovery gaps, settings, and audit events.
- Application logs must be structured.
- Health checks must cover Bybit, the ranking provider, PostgreSQL, and
  Telegram.
- Repeated upstream failures must generate a service warning without flooding
  Telegram.
- Health is tracked per instrument and timeframe. A global healthy status must
  not hide stale data for an individual instrument.

## 16. Deployment

- Initial environment: local Windows machine with Docker Desktop.
- Services are deployed with Docker Compose.
- Secrets are supplied through environment variables and are never committed.
- Production deployment to a VPS must use the same container images and
  configuration model.

## 17. Validation

A historical backtest is not required before the first live observation
period. Virtual tracking is mandatory from day one.

The strategy must not be considered ready for automatic execution until it has
at least 100 completed virtual signals and demonstrates:

- Positive expectancy after estimated costs.
- Profit Factor greater than 1.3.
- Maximum simulated drawdown below 15%.
- Results not dominated by one instrument.
- No unresolved data-quality or signal-duplication defects.
- Ambiguous virtual-trade outcomes are reported separately and cannot silently
  improve performance metrics.

Positive total profit by itself is not sufficient.

## 18. Out of scope for v1

- Real order placement.
- Automatic position management.
- News analysis.
- Copy trading.
- Multi-user billing or permissions.
- A web dashboard.

A read-only web dashboard may be considered after the Telegram MVP and virtual
tracking are stable.

## 19. Acceptance criteria

The MVP is accepted when it can:

1. Refresh the eligible market universe automatically.
2. Continuously ingest the required Bybit market data.
3. Detect and persist deterministic SMC structures.
4. Score candidates and suppress ineligible or duplicate signals.
5. Send localized Telegram signals only during the configured schedule.
6. Track every signal through its virtual lifecycle.
7. Report current status, active signals, settings, and performance.
8. Restart without losing state or generating duplicate notifications.
9. Recover missing market data before an affected instrument can emit signals.
10. Resolve live virtual trades from ordered market events and conservatively
    classify unresolved intrabar conflicts.
11. Enforce global notification limits and the BTC volatility circuit breaker.
