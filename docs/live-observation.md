# Live observation

An evaluation window freezes one immutable strategy version and defines the
time boundary used for live statistics. Only one window may be active.

Current observation reports are based on virtual outcomes. Live execution data
is stored in `live_executions`, and Telegram live-close messages include Bybit
real PnL when it is available, but full live-vs-virtual analytics are not yet
part of the observation report.

## Commands

```powershell
docker compose run --rm api python -m crypto_smc.observation start `
  --name live-2026-06 `
  --strategy-version smc-v1.0.0

docker compose run --rm api python -m crypto_smc.observation status

docker compose run --rm --volume "${PWD}/data:/app/data" api `
  python -m crypto_smc.observation report `
  --output /app/data/live-report.json

docker compose run --rm api python -m crypto_smc.observation close
```

The selected version is made active atomically when the window starts.
Changing it while a window is active is rejected. Close the window before
deploying a new version, then start a new evaluation window.

## Report contract

The report includes:

- completed, entered, and not-entered signals;
- wins, losses, breakeven, and ambiguous outcomes;
- net PnL, gross profit/loss, expectancy, average R, and Profit Factor;
- fees and estimated funding;
- maximum drawdown from the frozen reference balance;
- grouping by symbol, direction, score band, and UTC trading session;
- suppressed-candidate reason counts;
- unresolved data gaps and public-trade coverage failures.

Eligibility for an execution review requires all of these:

- at least 100 completed virtual signals;
- positive expectancy after costs;
- Profit Factor above 1.3;
- drawdown below 15%;
- no symbol exceeding 35% of completed outcomes;
- no unresolved data gaps or coverage failures.

The verdict is evidence for strategy review. It does not automatically change
live execution flags or risk limits.

## Live versus replay

```powershell
docker compose run --rm --volume "${PWD}/data:/app/data" api `
  python -m crypto_smc.observation compare `
  --replay-report /app/data/replay-output/report.json `
  --output /app/data/live-vs-replay.json
```

Comparison is rejected unless the strategy version and parameter checksum
match exactly. It reports deltas for signal frequency, acceptance rate,
average score, score-band shares, entry rate, win rate, ambiguity, average R,
Profit Factor, and drawdown. Results remain `preliminary` while the live window
is shorter than 24 hours, either side has fewer than 30 completed outcomes, or
the replay contains no market rows.

## Live execution notes

For operational live testing, compare three sources:

1. Telegram `LIVE: ...` messages.
2. Bybit account history and closed PnL.
3. Database rows in `live_executions`.

Virtual PnL can diverge from Bybit real PnL because live execution uses actual
market fills, slippage guards, exchange stops, and Bybit fee/PnL accounting.
Real account results must be taken from Bybit closed PnL.
