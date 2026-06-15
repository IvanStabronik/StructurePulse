# Signal protection and lifecycle

Accepted strategy candidates are converted into durable signal records inside
the same PostgreSQL transaction as the analysis snapshot and candidates.

## Initial publication

The policy checks, in order:

1. Candidate validity and expiration.
2. BTC abnormal-movement circuit breaker.
3. One active signal per symbol.
4. Per-symbol cooldown.
5. Maximum active portfolio signals.
6. Rolling hourly publication limit.
7. Short burst limit.

Allowed signals start as `preparing`. They are not considered publishable to a
user until trade-stream coverage proves that no trade was missed during the
subscription handshake. Policy rejections are persisted as `suppressed` with
their reason for auditability.

Defaults:

```text
cooldown=60 minutes
maximum active=5
maximum per hour=10
burst maximum=3 per 5 minutes
pause on abnormal BTC=true
```

All values are configurable through the `SIGNAL_*` environment variables in
`.env.example`.

## Persistence

- `signals` stores immutable price levels plus current lifecycle state.
- `signal_events` is the append-only transition history. Every event has a
  globally unique idempotency key.
- `virtual_trades` stores the current virtual position and final PnL fields.

A PostgreSQL partial unique index prevents more than one `preparing`, `active`,
`entered`, or `tp1_reached` signal for the same symbol. A transaction-scoped
advisory lock serializes portfolio-limit decisions.

State transitions lock the signal and virtual trade rows, validate both state
machines, increment their versions, and append the event in one transaction.
Repeating the same idempotency key for the same signal is a no-op; reusing it
for another signal is an error.

## Public-trade coverage

The worker opens a dedicated `publicTrade.{symbol}` WebSocket only while a
signal for that symbol is being tracked. A stream is considered ready only
after its first trade has been buffered, not merely after Bybit acknowledges
the subscription.

For initial activation and reconnect recovery, the worker:

1. Records the coverage anchor before opening the subscription.
2. Buffers WebSocket trades by Bybit trade ID.
3. Loads up to 1,000 recent public trades through REST.
4. Requires REST history to reach the anchor.
5. Requires at least one identical trade ID in REST and WebSocket data.
6. Deduplicates the merged data by trade ID and replays it chronologically.

If this proof fails, the system fails closed. A signal waiting for entry becomes
`coverage_failed`. A signal that was already active is recovered from canonical
closed 1m candles once the `kline_1m` checkpoint is `ready` and the complete
minute range is continuous. Public trade checkpoints are stored on the virtual
trade so a worker restart resumes from the last processed trade instead of
duplicating transitions.

The candle fallback is deliberately conservative:

- Entry and Stop Loss in the same candle resolve stop-first as `ambiguous`.
- Stop Loss and either target in the same candle resolve stop-first as
  `ambiguous`.
- Stop Loss and TP2 after TP1 resolve at fee-adjusted breakeven and remain
  `ambiguous`.
- A target touched in the entry candle is not credited unless its order after
  entry is provable.
- Missing, incomplete, or non-ready 1m data leaves recovery pending.

After the closed-candle range is processed, buffered public trades newer than
that range are replayed and exact trade tracking resumes.

## Virtual lifecycle

Every public trade is evaluated in exchange order against the exact signal
levels. The implemented lifecycle is:

```text
preparing -> active -> entered -> tp1_reached -> tp2_completed
                    \-> invalidated
                              \-> stopped
                              \-> stopped_at_breakeven
```

Waiting signals expire after their configured expiration time. TP1 realizes
half the position and moves the remaining stop to fee-adjusted breakeven.
Realized PnL, taker fees, remaining quantity, and R multiple are persisted with
the transition.

Funding uses the rate captured in the immutable strategy analysis and the
instrument funding interval. The estimate is prorated by actual holding time;
LONG pays positive funding, SHORT receives it, and quantity is halved after
TP1. `realized_pnl` and R multiple include the estimated funding, while fees
and funding remain separately stored for auditability.

## Debug API

When `DEBUG_API_ENABLED=true`:

```text
GET /debug/lifecycle-signals
GET /debug/lifecycle-signals?symbol=BTCUSDT&status=preparing
```

The endpoint exposes the signal and virtual-trade state without mutating it.
