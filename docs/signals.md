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
user until M6 trade-stream coverage proves that no trade was missed during the
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

## Debug API

When `DEBUG_API_ENABLED=true`:

```text
GET /debug/lifecycle-signals
GET /debug/lifecycle-signals?symbol=BTCUSDT&status=preparing
```

The endpoint exposes the signal and virtual-trade state without mutating it.
