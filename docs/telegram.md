# Telegram operation

## Configuration

Create a bot through BotFather, then set these values in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=replace-with-bot-token
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_DEFAULT_LANGUAGE=ru
TELEGRAM_SCHEDULE_TIMEZONE=Europe/Warsaw
TELEGRAM_SCHEDULE_START=07:00:00
TELEGRAM_SCHEDULE_END=20:00:00
```

Multiple allowed user IDs are comma-separated. Unknown users receive no
market data or command response.

If the token is empty, the Telegram container remains idle and logs that
delivery is disabled. This lets the rest of the local stack run before bot
credentials are configured.

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
