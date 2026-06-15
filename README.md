# Crypto SMC Signal Bot

Signal-only monitor for Bybit USDT Linear Perpetual futures. The project is in
the foundation phase: infrastructure, health checks, PostgreSQL migrations,
read-only Bybit instrument discovery, and a daily 30-asset market universe are
available. Closed 1m candles are streamed through sharded Bybit WebSockets and
recovered through REST with durable checkpoints, buffering, and gap tracking.
Canonical 1m data is deterministically aggregated into 5m, 15m, 1H, and 4H
candles through a durable priority queue with resumable historical rebuilds.
The pure synchronous `smc_core` package detects deterministic swings,
structure breaks, liquidity events, displacement, FVGs, Order Blocks, and
dealing ranges. The worker composes closed 4H/1H/15m/5m analyses into
auditable LONG and SHORT candidates with scoring, fee-aware risk sizing, and
suppression reasons. A deterministic offline replay command can run the same
analysis over historical 1m CSV data and produce auditable JSON/CSV reports.
Accepted candidates pass durable duplicate, cooldown, portfolio, burst, and
BTC circuit-breaker checks before entering the signal lifecycle.
Telegram delivery uses a PostgreSQL transactional outbox, Russian or English
messages, configurable score and schedule filters, and private commands for
explicitly allowed user IDs.

## Requirements

- Docker Desktop with Docker Compose.

Local Python is optional because all commands run in containers.

## Start

```powershell
Copy-Item .env.example .env
# Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS in .env.
docker compose build
docker compose run --rm migrate
docker compose up -d postgres api worker telegram
```

Open:

- Liveness: `http://localhost:8000/health/live`
- Readiness: `http://localhost:8000/health/ready`
- API metrics: `http://localhost:8000/metrics`
- Worker market-data metrics: `http://localhost:8001/metrics`
- Worker liveness: `http://localhost:8001/health/live`
- Worker readiness: `http://localhost:8001/health/ready`
- Current universe: `http://localhost:8000/universe/current`
- Market-data status: `http://localhost:8000/market-data/status`
- Aggregation status: `http://localhost:8000/aggregation/status`
- Debug instruments: `http://localhost:8000/debug/instruments`
- Debug candidates: `http://localhost:8000/debug/candidates`
- Debug accepted plans: `http://localhost:8000/debug/signals`
- Debug lifecycle signals: `http://localhost:8000/debug/lifecycle-signals`
- Active evaluation window: `http://localhost:8000/observation/current`
- Live evaluation report: `http://localhost:8000/observation/report`

Debug routes exist only when `DEBUG_API_ENABLED=true`.

## Offline replay

```powershell
docker compose run --rm --volume "${PWD}\data:/app/data" api `
  python -m crypto_smc.replay `
  --input /app/data/history.csv `
  --output-dir /app/data/replay-output
```

The CSV contract, chronology rules, conservative virtual outcomes, and report
fields are documented in `docs/replay.md`.

## Telegram

The bot accepts commands only from IDs listed in
`TELEGRAM_ALLOWED_USER_IDS`. New signal notifications follow each user's
configured schedule; lifecycle tracking and lifecycle notifications continue
outside that window. See `docs/telegram.md` for setup and commands.

## Operations

Run the smoke test after startup:

```powershell
docker run --rm crypto-smc-api python scripts/smoke_test.py `
  --api-url http://host.docker.internal:8000 `
  --worker-url http://host.docker.internal:8001
```

Backup, restore, outage drills, graceful shutdown, readiness semantics, and
retention are documented in `docs/operations.md`.

## Live observation

Start a strategy-frozen evaluation window:

```powershell
docker compose run --rm api python -m crypto_smc.observation start `
  --name live-2026-06 `
  --strategy-version smc-v1.0.0
```

Build a reproducible JSON report:

```powershell
docker compose run --rm --volume "${PWD}/data:/app/data" api `
  python -m crypto_smc.observation report `
  --output /app/data/live-report.json
```

The report groups results by symbol, direction, score band, and UTC trading
session. It includes costs, ambiguity, drawdown, suppressions, data-quality
failures, and a readiness verdict that remains `insufficient_sample` until at
least 100 virtual signals are complete.

## Quality checks

```powershell
docker compose run --rm api ruff check .
docker compose run --rm api ruff format --check .
docker compose run --rm api mypy
docker compose run --rm api pytest
docker compose config
docker compose run --rm api python scripts/profile_smc_core.py
```

## Database migrations

```powershell
docker compose run --rm migrate
docker compose run --rm api alembic downgrade -1
```

Do not run a downgrade against important data. Migration deployment rules are
documented in `docs/architecture.md`.
