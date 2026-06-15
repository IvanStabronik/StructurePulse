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
Telegram signal delivery is not implemented yet.

## Requirements

- Docker Desktop with Docker Compose.

Local Python is optional because all commands run in containers.

## Start

```powershell
Copy-Item .env.example .env
docker compose build
docker compose run --rm migrate
docker compose up -d postgres api worker telegram
```

Open:

- Liveness: `http://localhost:8000/health/live`
- Readiness: `http://localhost:8000/health/ready`
- API metrics: `http://localhost:8000/metrics`
- Worker market-data metrics: `http://localhost:8001/metrics`
- Current universe: `http://localhost:8000/universe/current`
- Market-data status: `http://localhost:8000/market-data/status`
- Aggregation status: `http://localhost:8000/aggregation/status`
- Debug instruments: `http://localhost:8000/debug/instruments`
- Debug candidates: `http://localhost:8000/debug/candidates`
- Debug accepted plans: `http://localhost:8000/debug/signals`
- Debug lifecycle signals: `http://localhost:8000/debug/lifecycle-signals`

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
