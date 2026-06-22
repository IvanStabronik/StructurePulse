# Operations runbook

## Health model

- API `/health/live` proves the HTTP process is responsive.
- API `/health/ready` requires PostgreSQL and the configured Alembic revision.
- Worker `/health/live` proves its event loop is responsive.
- Worker `/health/ready` additionally requires PostgreSQL, the expected schema,
  complete market-data recovery, and a non-quiescing runtime.
- Metrics are exposed through API `/metrics` and worker `/metrics`.

During shutdown the worker first clears readiness and pauses new strategy
cycles. Existing lifecycle tracking gets a short drain window, then all tasks
are cancelled and their stream `finally` blocks close WebSockets.

## Normal startup

```powershell
docker compose build
docker compose run --rm migrate
docker compose up -d postgres api worker telegram
docker run --rm crypto-smc-api python scripts/smoke_test.py `
  --api-url http://host.docker.internal:8000 `
  --worker-url http://host.docker.internal:8001
```

Worker readiness can remain `503` while history is warming or Bybit coverage
is recovering. This is fail-closed behavior for new signals.

After five continuous minutes without market-data readiness, the worker
creates a Telegram service warning through the transactional outbox. Warning
keys are bucketed to a 30-minute cooldown, and one recovery message is emitted
when readiness returns. `/pause` suppresses these proactive messages.

## Live execution startup checklist

Live execution is off unless all of these are set:

```dotenv
EXECUTION_ENABLED=true
EXECUTION_MODE=auto
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
```

For the current small-account live test, the intended local settings are:

```dotenv
EXECUTION_RISK_USDT=20
EXECUTION_MIN_RISK_USDT=5
EXECUTION_MAX_EFFECTIVE_LEVERAGE=45
EXECUTION_MAX_OPEN_POSITIONS=1
EXECUTION_MAX_TRADES_PER_DAY=5
EXECUTION_MAX_DAILY_LOSS_USDT=60
EXECUTION_MAX_SLIPPAGE_BPS=20
```

Verify account and execution settings:

```powershell
docker compose run --rm worker python -m crypto_smc.execution.check_bybit_account
```

Inspect the latest persisted live attempts:

```powershell
docker compose exec -T postgres psql -U crypto_smc -d crypto_smc -c `
  "select signal_id, status, symbol, side, risk_usdt, quantity, realized_pnl_usdt, error_message, updated_at from live_executions order by id desc limit 10;"
```

Use the Bybit UI as the final account source of truth when balances or
positions look surprising.

## Live execution interpretation

Telegram may show both virtual and live messages:

- `VIRTUAL ENTRY`, `VIRTUAL TP1`, and `VIRTUAL RESULT` describe the strategy
  model.
- `LIVE: ...` messages describe Bybit execution attempts.
- `LIVE: LIMIT ORDER PLACED` means a real GTC limit entry order is resting on
  Bybit. It is not an open position yet.
- `LIVE: POSITION OPEN` means Bybit reports a non-zero position and the bot has
  set the protective stop.
- `LIVE: VIRTUAL ONLY` means the bot intentionally did not place an order and
  kept tracking the signal virtually.
- `pending entry cancelled` means the real limit entry did not fill before the
  virtual signal expired or was invalidated, so the bot cancelled the order.
- `Real PnL` in a live-close message comes from Bybit closed PnL.

Virtual PnL and real PnL can differ. Real PnL is the account result.

Telegram `/settings` shows user notification and virtual-reference settings.
It does not change `.env` live execution risk.

## Normal shutdown

```powershell
docker compose stop -t 30 worker telegram api
```

Confirm `service_stopped` in logs. Do not run schema migrations while the
worker is still ready.

## Database backup

Create a compressed logical backup:

```powershell
docker compose exec -T postgres pg_dump `
  -U crypto_smc -d crypto_smc -Fc > structurepulse.dump
```

Record the application commit and Alembic revision beside every backup:

```powershell
git rev-parse HEAD
docker compose run --rm api alembic current
```

## Database restore drill

Restores are destructive. Use a separate database or disposable Compose
volume for drills.

```powershell
docker compose down
docker volume create structurepulse-restore-test
docker run --rm -d --name structurepulse-restore-db `
  -e POSTGRES_DB=crypto_smc `
  -e POSTGRES_USER=crypto_smc `
  -e POSTGRES_PASSWORD=crypto_smc `
  -v structurepulse-restore-test:/var/lib/postgresql/data `
  postgres:16-alpine
docker exec -i structurepulse-restore-db pg_restore `
  -U crypto_smc -d crypto_smc --clean --if-exists < structurepulse.dump
```

After restore, run `alembic current`, start the worker, and expect warming
until checkpoints and missing candles are reconciled.

## Outage drills

PostgreSQL outage:

1. Run `docker compose stop postgres`.
2. Verify API and worker readiness return `503`.
3. Verify no new signals are created.
4. Start PostgreSQL and verify readiness recovers after market-data checks.

Worker restart:

1. Record active signals and their latest event IDs.
2. Run `docker compose restart worker`.
3. Verify no duplicate signal events or Telegram deliveries appear.
4. Verify public-trade overlap or conservative candle fallback restores
   lifecycle tracking.

Bybit outage:

1. Temporarily set an unreachable `BYBIT_WS_URL` and restart the worker.
2. Verify worker readiness stays `503` and new signals remain disabled.
3. Restore the URL and verify reconnect, backfill, and readiness recovery.

## Retention

Maintenance deletes old candles in bounded batches. Defaults retain 1m data
for 180 days and aggregate data for 730 days. Signal snapshots, candidates,
events, virtual trades, and notification audit records are not deleted.

Increase retention before long-horizon research. Back up the database before
reducing either retention period.
