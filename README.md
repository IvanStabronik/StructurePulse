# Crypto SMC Signal Bot

Signal-only monitor for Bybit USDT Linear Perpetual futures. The project is in
the foundation phase: infrastructure, health checks, PostgreSQL migrations,
read-only Bybit instrument discovery, and a daily 30-asset market universe are
available. Closed 1m candles are recovered through Bybit REST with durable
checkpoints and gap tracking. WebSocket ingestion, strategy, and Telegram
signal delivery are not implemented yet.

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
- Metrics: `http://localhost:8000/metrics`
- Current universe: `http://localhost:8000/universe/current`
- Market-data status: `http://localhost:8000/market-data/status`
- Debug instruments: `http://localhost:8000/debug/instruments`

Debug routes exist only when `DEBUG_API_ENABLED=true`.

## Quality checks

```powershell
docker compose run --rm api ruff check .
docker compose run --rm api ruff format --check .
docker compose run --rm api mypy
docker compose run --rm api pytest
docker compose config
```

## Database migrations

```powershell
docker compose run --rm migrate
docker compose run --rm api alembic downgrade -1
```

Do not run a downgrade against important data. Migration deployment rules are
documented in `docs/architecture.md`.
