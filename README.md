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
dealing ranges. Strategy composition and Telegram signal delivery are not
implemented yet.

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

Debug routes exist only when `DEBUG_API_ENABLED=true`.

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
