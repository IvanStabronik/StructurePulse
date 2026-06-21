# Telegram operation

## Configuration

Create a bot through BotFather, then set these values in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=replace-with-bot-token
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_DEFAULT_LANGUAGE=ru
TELEGRAM_SCHEDULE_TIMEZONE=Europe/Warsaw
TELEGRAM_SCHEDULE_START=00:00:00
TELEGRAM_SCHEDULE_END=00:00:00
```

Multiple allowed user IDs are comma-separated. Unknown users receive no
market data or command response.

If the token is empty, the Telegram container remains idle and logs that
delivery is disabled. This lets the rest of the local stack run before bot
credentials are configured.

The current local live-test setup often uses `00:00-00:00 Europe/Warsaw` as a
24-hour notification window. This affects message delivery only. It does not
stop market-data ingestion or strategy analysis.

## Commands

- `/signals` lists active signals.
- `/coin BTC` or `/coin BTCUSDT` shows the latest BTCUSDT analysis.
- `/settings` shows current notification and risk settings.
- `/status` shows service and outbox state.
- `/stats` shows virtual-trade statistics.
- `/language ru|en` changes the response and notification language.
- `/threshold 70` sets the minimum signal score.
- `/schedule 07:00 20:00 Europe/Warsaw` changes the new-signal window.
- `/risk 1 10000` sets risk percent and reference balance.
- `/pause` suppresses proactive Telegram delivery.
- `/resume` resumes proactive Telegram delivery.

Direct command replies are sent immediately. Signal and lifecycle
notifications use the PostgreSQL outbox. Known transient failures use bounded
backoff. A network failure with an unknown Telegram outcome is not retried
blindly, which avoids duplicate logical messages.

The schedule applies only to new entry signals. Market tracking continues at
all times, and entry, target, stop, expiration, ambiguity, and service
warnings can still be delivered outside the new-signal window.

## Settings versus live execution

`/settings` shows per-user Telegram and virtual-reference values:

- language;
- minimum score;
- notification schedule;
- virtual risk percent;
- reference balance;
- pause state.

It does not show or change live execution values from `.env`.

Live execution uses:

```dotenv
EXECUTION_RISK_USDT
EXECUTION_MIN_RISK_USDT
EXECUTION_MAX_EFFECTIVE_LEVERAGE
EXECUTION_MAX_OPEN_POSITIONS
EXECUTION_MAX_TRADES_PER_DAY
EXECUTION_MAX_DAILY_LOSS_USDT
EXECUTION_MAX_SLIPPAGE_BPS
```

Use this command to inspect live execution settings:

```powershell
docker compose run --rm worker python -m crypto_smc.execution.check_bybit_account
```

## Message types

Virtual messages:

- `НОВЫЙ СИГНАЛ` / `NEW SIGNAL`: accepted setup and virtual risk.
- `ВИРТУАЛЬНЫЙ ВХОД` / `VIRTUAL ENTRY`: virtual lifecycle touched entry.
- `ВИРТУАЛЬНЫЙ TP1` / `VIRTUAL TP1`: virtual TP1 was reached.
- `ВИРТУАЛЬНЫЙ РЕЗУЛЬТАТ` / `VIRTUAL RESULT`: virtual terminal result.

Live messages:

- `LIVE: SUBMITTING ORDER`: worker is preparing a real order.
- `LIVE: POSITION OPEN`: Bybit entry order was accepted and position size was
  detected.
- `LIVE: TP1 HALF CLOSED`: reduce-only TP1 close was submitted.
- `LIVE: POSITION CLOSED`: live position is closed.
- `LIVE: EXECUTION FAILED`: live execution was skipped or failed.

When Bybit closed PnL is available, `LIVE: POSITION CLOSED` includes:

- `Real PnL`;
- `Real entry`;
- `Real exit`.

If `LIVE: EXECUTION FAILED` contains `live entry skipped`, no Bybit order was
sent. This is usually a slippage guard, not a system crash.
