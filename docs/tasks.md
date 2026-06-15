# Crypto SMC Signal Bot - Implementation Plan v1.1

## 1. Delivery strategy

Development is split into vertical milestones. Each milestone must leave the
system runnable and testable. Market-data correctness and signal auditability
come before Telegram presentation or strategy optimization.

No real order execution is included.

## 2. Definition of Done

A task is complete when:

- Code is typed and passes formatting and static checks.
- Unit tests cover domain behavior and relevant edge cases.
- Database changes include an Alembic migration.
- Configuration and operational behavior are documented.
- Logs and metrics make failures diagnosable.
- No secret or machine-specific value is committed.
- Acceptance criteria are demonstrated locally with Docker Compose.

## 3. Milestones

### M0 - Project foundation

Goal: create a reproducible local development environment.

Tasks:

- `M0-01` Create Python 3.12 project structure and package metadata.
- `M0-02` Configure uv, Ruff, mypy, pytest, and pytest-asyncio.
- `M0-03` Add application settings with environment-variable validation.
- `M0-04` Add structured JSON logging and correlation IDs.
- `M0-05` Create Dockerfile and Docker Compose services for PostgreSQL, API,
  worker, Telegram, and migrations.
- `M0-06` Configure SQLAlchemy async sessions and Alembic.
- `M0-07` Add FastAPI liveness, readiness, and Prometheus metrics endpoints.
- `M0-08` Add CI commands for linting, typing, tests, and migration checks.
- `M0-09` Create `.env.example` and local startup documentation.
- `M0-10` Add a development-only debug API boundary protected by an explicit
  environment flag. It must be disabled by default outside local development.

Acceptance:

- `docker compose up` starts PostgreSQL and healthy application containers.
- A migration can create and downgrade a test table.
- Lint, type-check, and test commands pass.
- Debug routes are unavailable unless explicitly enabled.

### M1 - Instruments and market universe

Goal: produce a durable daily list of 30 eligible Bybit contracts.

Tasks:

- `M1-01` Define normalized provider models for assets and instruments.
- `M1-02` Implement Bybit instrument and ticker REST adapters.
- `M1-03` Implement CoinGecko market-ranking adapter with cache support.
- `M1-04` Create instrument, universe snapshot, and universe member tables.
- `M1-05` Implement stablecoin and excluded-asset classification.
- `M1-06` Implement Bybit intersection and liquidity/data-quality filters.
- `M1-07` Implement versioned universe refresh with PostgreSQL advisory lock.
- `M1-08` Preserve the previous universe when the ranking provider fails.
- `M1-09` Expose universe status and filter reasons through the API.

Acceptance:

- A refresh selects no more than 30 eligible active USDT perpetual contracts.
- Every included and excluded asset has a persisted reason.
- Provider failure leaves the last valid universe active.

### M2 - Market-data ingestion and backfill

Goal: maintain complete, durable closed 1m candles for the active universe.

Status: completed on 2026-06-14. The implementation uses two configurable
WebSocket shards for the initial 30-symbol universe, buffers closed candles
during synchronous REST recovery, and exposes worker metrics on port 8001.

Tasks:

- `M2-01` Create candle, checkpoint, and data-gap tables.
- `M2-02` Implement the Bybit REST kline adapter.
- `M2-03` Implement a shared adaptive Bybit REST rate limiter.
- `M2-04` Respect Bybit rate-limit headers and HTTP 429 retry instructions.
- `M2-05` Add bounded concurrency, priority queues, backoff, and jitter.
- `M2-06` Implement sharded Bybit WebSocket connection management.
- `M2-07` Ingest and idempotently upsert closed 1m candles.
- `M2-08` Persist checkpoints and detect missing time ranges.
- `M2-09` Implement startup and reconnect backfill.
- `M2-10` Buffer WebSocket events during REST recovery.
- `M2-11` Merge and deduplicate REST and WebSocket data.
- `M2-12` Add per-symbol warming, ready, degraded, and recovering states.
- `M2-13` Add freshness, queue, reconnect, rate-limit, and gap metrics.

Backfill priority:

1. BTCUSDT and ETHUSDT.
2. Instruments with pending or active signals.
3. Remaining universe instruments by capitalization rank.

Acceptance:

- Restart after a simulated 30-minute outage repairs every candle gap.
- Replayed or duplicated events do not create duplicate candles.
- A symbol cannot generate signals until its required history is ready.
- HTTP 429 responses slow recovery without crashing or busy-looping.

### M3 - Candle aggregation

Goal: create deterministic 5m, 15m, 1H, and 4H candles from canonical 1m data.

Status: completed on 2026-06-14. Historical rebuilds are resumable and bounded,
live intervals have queue priority, incomplete intervals are withheld, and
sampled aggregates are reconciled against Bybit REST.

Tasks:

- `M3-01` Create aggregated-candle storage and uniqueness constraints.
- `M3-02` Implement UTC exchange-time interval boundaries.
- `M3-03` Implement OHLCV aggregation for every required timeframe.
- `M3-04` Rebuild aggregates when canonical candles are repaired.
- `M3-05` Detect incomplete source intervals and withhold invalid aggregates.
- `M3-06` Reconcile sampled aggregates against Bybit REST data.
- `M3-07` Add aggregation lag and repair metrics.
- `M3-08` Split historical rebuilds into bounded symbol and time-range batches.
- `M3-09` Process live closed candles through a higher-priority queue than
  historical aggregation work.
- `M3-10` Add configurable CPU, queue-depth, and transaction-size budgets for
  rebuild jobs.
- `M3-11` Persist rebuild progress so interrupted historical aggregation can
  resume without starting over.

Acceptance:

- Fixture-based aggregates exactly match expected OHLCV values.
- Missing 1m candles prevent a higher-timeframe candle from becoming ready.
- Repairing a 1m candle deterministically repairs dependent aggregates.
- A one-day, 30-symbol recovery remains within configured live-event latency
  while historical batches complete in the background.
- Restarting an interrupted rebuild resumes from its durable checkpoint.

### M4 - SMC primitives

Goal: implement deterministic, independently testable market structures.

Status: completed on 2026-06-14. `smc_core` is a pure synchronous package with
immutable inputs and outputs, mirrored fixture coverage, explicit boundary
rules, a bounded process-pool adapter, and a reproducible 10,000-candle
profiling script.

Tasks:

- `M4-00` Create `smc_core` as a synchronous pure domain package with no
  imports from Bybit adapters, SQLAlchemy, FastAPI, asyncio, Telegram, or
  application service modules.
- `M4-01` Define immutable candle and structure domain models.
- `M4-02` Implement ATR and rolling-statistic utilities.
- `M4-03` Implement timeframe-specific confirmed Swing High/Low detection.
- `M4-04` Implement BOS detection.
- `M4-05` Implement CHOCH/MSS detection.
- `M4-06` Implement liquidity sweep detection.
- `M4-07` Implement Equal Highs/Lows detection with ATR tolerance.
- `M4-08` Implement FVG detection and lifecycle.
- `M4-09` Implement displacement detection.
- `M4-10` Implement Order Block detection and invalidation.
- `M4-11` Implement dealing range and Premium/Discount classification.
- `M4-12` Add mirrored LONG/SHORT and boundary-condition tests.
- `M4-13` Profile calculations and move heavy batches to the process pool.
- `M4-14` Add architecture tests that fail when `smc_core` imports forbidden
  infrastructure or application packages.
- `M4-15` Define synchronous array-oriented APIs suitable for direct fixture,
  replay-runner, and process-pool use.

Acceptance:

- Every primitive has fixed positive, negative, and boundary fixtures.
- Repeated analysis of identical input produces identical output.
- Strategy jobs do not exceed the event-loop lag budget.
- `smc_core` can be installed and tested without PostgreSQL, network access, or
  an event loop.

### M5 - Strategy, scoring, and risk

Goal: convert market structures into auditable signal candidates.

Status: completed on 2026-06-15. The implementation includes versioned
snapshots, mirrored strategy composition, scoring, market filters, fee-aware
risk sizing, persistence, live process-pool analysis, filtered debug APIs, and
a deterministic offline CSV replay with conservative 1m outcomes and JSON/CSV
reports. A 43,200-row one-month smoke run completed in 23.5 seconds.

Tasks:

- `M5-01` Create immutable strategy-version and analysis-snapshot tables.
- `M5-02` Implement 4H/1H context evaluation.
- `M5-03` Implement 15m setup evaluation.
- `M5-04` Implement mandatory 5m entry confirmation.
- `M5-05` Implement LONG and mirrored SHORT setup composition.
- `M5-06` Implement the weighted 0-100 score and evidence list.
- `M5-07` Add volume, Open Interest, funding, spread, and volatility filters.
- `M5-08` Implement BTC abnormal-movement warning and score adjustment.
- `M5-09` Calculate entry zone, Stop Loss, invalidation, TP1, and TP2.
- `M5-10` Calculate 1% risk position size for a 10,000 USDT reference balance.
- `M5-11` Estimate fees, reward-to-risk, margin, and leverage safety.
- `M5-12` Reject candidates below score 70 or net reward-to-risk 1:3.
- `M5-13` Persist accepted and suppressed candidates with reasons.
- `M5-14` Implement `GET /debug/candidates` and `GET /debug/signals` JSON
  endpoints for accepted, suppressed, and active records.
- `M5-15` Add filters for symbol, direction, score, strategy version, and time
  range to the debug endpoints.
- `M5-16` Implement a deterministic offline replay runner that reads historical
  closed 1m candles from CSV without Bybit, Telegram, or live WebSockets.
- `M5-17` Reuse the production aggregation, `smc_core`, scoring, risk, and
  virtual-lifecycle code paths in replay mode.
- `M5-18` Support at least one month of replay data, chronological event
  processing, fixed strategy configuration, and reproducible output.
- `M5-19` Produce JSON and CSV replay reports containing candidates,
  suppressions, virtual outcomes, score bands, R multiples, fees, drawdown,
  Profit Factor, and ambiguity counts.
- `M5-20` Add fixture-based replay tests that detect look-ahead bias and use
  only information available at each simulated timestamp.

Acceptance:

- Every score can be reconstructed from persisted components.
- Suggested loss at Stop Loss does not exceed configured risk.
- Unsafe 20x leverage produces a warning or lower recommendation.
- No setup uses an unfinished candle.
- Debug endpoints expose structured evidence without requiring direct SQL
  access and never mutate trading state.
- Replaying identical CSV data and configuration produces identical results.
- Replay processing cannot access candles later than the simulated clock.

### M6 - Signal protection and lifecycle

Goal: publish unique signals and track virtual outcomes accurately.

Status: in progress. M6-01 through M6-11 and M6-14 were completed on
2026-06-15. Signal publication is atomic; public trades use dynamic
subscriptions with REST overlap proof, identity-based merge, fail-closed
coverage handling, exact trade-by-trade lifecycle transitions, durable
checkpoints, and restart recovery. M6-12 remains open. M6-13 includes realized
PnL, taker fees, and R multiple; estimated funding remains open.

Tasks:

- `M6-01` Create signal, signal event, and virtual trade tables.
- `M6-02` Implement the signal and virtual-trade state machines.
- `M6-03` Implement one-active-signal-per-symbol and cooldown rules.
- `M6-04` Implement rolling portfolio and burst notification limits.
- `M6-05` Implement the BTC global circuit breaker.
- `M6-06` Implement dynamic Bybit public-trade subscriptions.
- `M6-07` Implement pre-subscription timestamp and REST trade overlap.
- `M6-08` Merge ordered REST and buffered WebSocket trades by trade identity.
- `M6-09` Suppress publication when continuous ordered coverage is unproven.
- `M6-10` Implement entry, invalidation, Stop Loss, TP1, breakeven, and TP2.
- `M6-11` Implement 90-minute signal expiration.
- `M6-12` Implement conservative 1m fallback and ambiguous classification.
- `M6-13` Calculate fees, funding estimates, R multiple, and final result.
- `M6-14` Recover active lifecycle tracking after restart.

Acceptance:

- Tests prove correct ordering when entry, stop, and target are close together.
- A handshake-gap trade is recovered through the REST overlap.
- Ambiguous cases cannot be counted as optimistic wins.
- Restart does not duplicate state transitions.

### M7 - Telegram and transactional outbox

Goal: deliver localized, idempotent signals and bot commands.

Tasks:

- `M7-01` Create notification outbox and user settings tables.
- `M7-02` Commit accepted signals and outbox events transactionally.
- `M7-03` Implement idempotent outbox polling, retry, and failure handling.
- `M7-04` Restrict commands to configured Telegram user IDs.
- `M7-05` Implement Russian and English localization.
- `M7-06` Render new-signal, warning, lifecycle, and service messages.
- `M7-07` Implement `/signals`, `/coin`, `/settings`, and `/status`.
- `M7-08` Implement `/language`, `/threshold`, `/schedule`, and `/risk`.
- `M7-09` Implement `/pause`, `/resume`, and `/stats`.
- `M7-10` Enforce the Europe/Warsaw 07:00-20:00 notification schedule.
- `M7-11` Continue lifecycle tracking outside notification hours.

Acceptance:

- Retrying an outbox record never sends the same logical message twice.
- Unauthorized Telegram users receive no private market information.
- Commands work in Russian and English.
- New entry signals are not sent outside the configured schedule.

### M8 - Operations and hardening

Goal: make the local MVP stable enough for continuous observation.

Tasks:

- `M8-01` Complete health and readiness dependency checks.
- `M8-02` Add bounded operational warnings without Telegram flooding.
- `M8-03` Add event-loop lag and process-pool saturation monitoring.
- `M8-04` Add database and candle-retention maintenance jobs.
- `M8-05` Add graceful shutdown and worker quiescence.
- `M8-06` Document expand-and-contract migration rules.
- `M8-07` Add migration lock and statement timeouts.
- `M8-08` Add backup and restore instructions for PostgreSQL.
- `M8-09` Run outage, reconnect, restart, and stale-data drills.
- `M8-10` Add end-to-end replay fixtures and a smoke-test command.
- `M8-11` Prepare local operator runbook.

Acceptance:

- Planned restart recovers without duplicate signals or missing lifecycle state.
- PostgreSQL or Bybit outages fail closed for new signals.
- The operator can identify stale symbols and recovery progress.
- A database restore can bring the service back to a consistent warming state.

### M9 - Live observation

Goal: compare trustworthy live virtual results with offline replay behavior and
evaluate the strategy.

Tasks:

- `M9-01` Run the system continuously with the 30-asset universe.
- `M9-02` Review suppressed candidates and false-positive patterns.
- `M9-03` Review data gaps, ambiguous outcomes, and operational alerts.
- `M9-04` Produce performance by symbol, direction, score band, and session.
- `M9-05` Freeze strategy versions during each evaluation window.
- `M9-06` Evaluate readiness after at least 100 completed virtual signals.
- `M9-07` Compare live signal frequency, score distribution, and outcomes with
  replay results for equivalent strategy versions.

Acceptance:

- Performance includes fees, estimated funding, and ambiguous-case reporting.
- Results can be reproduced from persisted inputs and strategy configuration.
- No automatic execution decision is made solely from positive total profit.
- Replay results shorten the feedback loop but do not replace the requirement
  for at least 100 completed live virtual signals before automatic execution is
  considered.

## 4. Recommended execution order

```text
M0
 -> M1
 -> M2
 -> M3
 -> M4
 -> M5
 -> M6
 -> M7
 -> M8
 -> M9
```

Some work may overlap:

- M4 starts immediately after M0 and is developed independently of M1-M3.
- M7 localization and command routing can begin after M0.
- M8 observability grows incrementally across every milestone.

Execution has two streams that join at M5:

```text
Infrastructure/data: M0 -> M1 -> M2 -> M3
Pure strategy core:   M0 -> M4
Join:                 M3 + M4 -> M5 -> M6
```

The critical path is `M0 -> M1 -> M2 -> M3 -> M5 -> M6`, with M4 completed in
parallel before M5 integration.

## 5. First implementation slice

The first coding slice should contain only M0 and a narrow part of M1:

1. Project skeleton and tooling.
2. Docker Compose with PostgreSQL.
3. Settings, logging, health, and migrations.
4. Normalized provider interfaces.
5. Read-only Bybit instrument discovery.
6. Development-only debug API boundary.
7. Empty `smc_core` package with enforced dependency rules.
8. Tests and local startup instructions.

This slice proves the development environment and external adapter boundaries
before market-data concurrency or trading logic is introduced.
