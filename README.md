# StructurePulse

StructurePulse is a Bybit USDT linear perpetual futures bot for deterministic
SMC-style market monitoring, Telegram signals, virtual trade tracking, and
optional live execution.

The bot is built as a Python 3.12 modular monolith with Docker Compose and
PostgreSQL. It continuously ingests Bybit market data, builds 5m/15m/1H/4H
candles from canonical 1m data, scores LONG and SHORT candidates, tracks every
signal virtually, and can place real orders when live execution is explicitly
enabled.

This is an experimental trading assistant, not financial advice.

## Current Capabilities

- Daily 30-symbol universe from CoinGecko ranking intersected with Bybit USDT
  perpetual instruments.
- Bybit public WebSocket ingestion and REST backfill/recovery.
- Deterministic SMC primitives in pure `smc_core`.
- Strategy scoring with accepted and suppressed candidates.
- Telegram notifications and private commands in Russian or English.
- PostgreSQL transactional outbox for reliable Telegram delivery.
- Virtual lifecycle tracking from ordered public trades.
- Optional Bybit live execution in `auto` mode.
- Live execution guards for margin, risk, max positions, max trades per day,
  max daily loss, leverage, and slippage.
- Bybit real closed PnL shown in live-close Telegram messages when available.
- Offline CSV replay and live observation reports.

## Documentation

- Requirements: `docs/requirements.md`
- Architecture: `docs/architecture.md`
- Operations: `docs/operations.md`
- Telegram: `docs/telegram.md`
- Strategy: `docs/strategy.md`
- SMC core: `docs/smc-core.md`
- Replay: `docs/replay.md`
- Live observation: `docs/live-observation.md`
- Implementation plan: `docs/tasks.md`

## Requirements

- Docker Desktop with Docker Compose.
- Telegram bot token for Telegram mode.
- Bybit API key and secret only if live execution is enabled.

Local Python is optional because development commands can run in containers.

## Start

```powershell
Copy-Item .env.example .env
docker compose build
docker compose run --rm migrate
docker compose up -d postgres api worker telegram
```

Configure Telegram in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=replace-with-token
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_DEFAULT_LANGUAGE=ru
TELEGRAM_SCHEDULE_TIMEZONE=Europe/Warsaw
```

## Live Execution

Live execution is disabled by default.

To enable automatic live testing:

```dotenv
BYBIT_API_KEY=replace-with-key
BYBIT_API_SECRET=replace-with-secret
BYBIT_ACCOUNT_TYPE=UNIFIED

EXECUTION_ENABLED=true
EXECUTION_MODE=auto
EXECUTION_RISK_USDT=20
EXECUTION_MIN_RISK_USDT=5
EXECUTION_MAX_EFFECTIVE_LEVERAGE=50
EXECUTION_MAX_OPEN_POSITIONS=1
EXECUTION_MAX_TRADES_PER_DAY=5
EXECUTION_MAX_DAILY_LOSS_USDT=60
EXECUTION_MAX_SLIPPAGE_BPS=20
```

Check live account settings without printing secrets:

```powershell
docker compose run --rm worker python -m crypto_smc.execution.check_bybit_account
```

Important: Telegram `/settings` shows user notification and virtual-reference
settings. Live execution risk is controlled by `.env`.

## Useful URLs

- API liveness: `http://localhost:8000/health/live`
- API readiness: `http://localhost:8000/health/ready`
- API metrics: `http://localhost:8000/metrics`
- Worker metrics: `http://localhost:8001/metrics`
- Worker liveness: `http://localhost:8001/health/live`
- Worker readiness: `http://localhost:8001/health/ready`
- Current universe: `http://localhost:8000/universe/current`
- Market-data status: `http://localhost:8000/market-data/status`
- Aggregation status: `http://localhost:8000/aggregation/status`
- Current observation: `http://localhost:8000/observation/current`
- Observation report: `http://localhost:8000/observation/report`

Debug routes exist only when `DEBUG_API_ENABLED=true`:

- `http://localhost:8000/debug/instruments`
- `http://localhost:8000/debug/candidates`
- `http://localhost:8000/debug/signals`
- `http://localhost:8000/debug/lifecycle-signals`

## Telegram Commands

- `/signals`
- `/coin BTC` or `/coin BTCUSDT`
- `/settings`
- `/status`
- `/stats`
- `/language ru|en`
- `/threshold 70`
- `/schedule 00:00 00:00 Europe/Warsaw`
- `/risk 1 10000`
- `/pause`
- `/resume`

## Quality Checks

```powershell
docker run --rm -v "${PWD}:/app" -w /app crypto-smc-worker ruff check --no-cache .
docker run --rm -v "${PWD}:/app" -w /app crypto-smc-worker ruff format --check --no-cache .
docker run --rm -v "${PWD}:/app" -w /app crypto-smc-worker mypy src
docker run --rm -v "${PWD}:/app" -w /app crypto-smc-worker pytest
docker compose config
```

## Offline Replay

```powershell
docker compose run --rm --volume "${PWD}\data:/app/data" api `
  python -m crypto_smc.replay `
  --input /app/data/history.csv `
  --output-dir /app/data/replay-output
```

## Live Observation

```powershell
docker compose run --rm api python -m crypto_smc.observation start `
  --name live-2026-06 `
  --strategy-version smc-v1.0.0

docker compose run --rm --volume "${PWD}/data:/app/data" api `
  python -m crypto_smc.observation report `
  --output /app/data/live-report.json
```

Live observation reports currently focus on virtual outcomes. Live execution
records exist separately, and Telegram live-close messages include Bybit real
PnL when available.

## Database Migrations

```powershell
docker compose run --rm migrate
docker compose run --rm api alembic current
```

Do not run downgrades against important data.
