# Offline replay

The replay command evaluates the production aggregation, `smc_core`, strategy,
scoring, and risk code over historical closed 1m candles without PostgreSQL,
Bybit, WebSockets, or Telegram.

## Input CSV

Required columns:

```text
symbol,open_time,open,high,low,close,volume,turnover
```

Optional market-context columns:

```text
open_interest,funding_rate,spread_bps,turnover_24h_usdt,instrument_max_leverage,instrument_quantity_step,instrument_min_notional
```

`open_time` accepts an ISO 8601 timestamp with timezone, Unix seconds, or Unix
milliseconds. Rows may be unordered; the loader sorts them by timestamp and
symbol. Duplicate `(symbol, open_time)` rows and malformed candle bodies are
rejected.

Contract metadata may be repeated on each row. When omitted, replay uses
conservative generic defaults (`100` maximum leverage, `0.00000001` quantity
step, and no minimum notional). Supplying Bybit metadata is recommended for
realistic position sizing.

Missing 1m candles are not invented. Any affected 5m, 15m, 1H, or 4H interval
is withheld by the production aggregation rules.

## Run

From the repository root:

```powershell
docker compose run --rm --volume "${PWD}\data:/app/data" api `
  python -m crypto_smc.replay `
  --input /app/data/history.csv `
  --output-dir /app/data/replay-output
```

Optional arguments:

- `--history-candles`: maximum closed candles retained per timeframe. Default:
  `300`.
- `--minimum-history-candles`: minimum closed candles required on every
  timeframe before evaluation. Default: `30`.

The command prints the summary as JSON and writes:

- `report.json`: fixed strategy parameters, candidates, evidence, input
  cutoffs, outcomes, and aggregate metrics.
- `candidates.csv`: accepted and suppressed candidates with risk levels and
  suppression reasons.
- `outcomes.csv`: virtual lifecycle results, PnL, fees, R multiple, and
  ambiguity flag.

## Chronology

Evaluation runs on each closed 5m boundary. A 5m, 15m, 1H, or 4H candle becomes
visible only when its `close_time` is at or before the simulated clock.
`input_cutoffs` in the report make this rule auditable.

Accepted candidates are resolved from subsequent 1m candles. If a stop and
target are both reachable inside the same minute, ordering is unknowable and
the result is marked `ambiguous`. Ambiguous events are resolved stop-first or
at zero PnL and can never improve Profit Factor.

The 1m lifecycle resolver is the canonical conservative fallback intended for
reuse by live signal tracking in M6.

## Capacity

A local smoke run on 2026-06-15 processed 43,200 rows (30 days for one symbol)
in 23.5 seconds in the project Docker image. Runtime scales with symbols,
candidate frequency, and the selected history window.
