# Strategy and Risk v1

The production strategy composes four closed-candle `smc_core` analyses:

- `4H` and `1H` define directional context and dealing-range location.
- `15m` requires a same-direction structural break with displacement, a
  liquidity sweep, and an open or partially filled FVG or Order Block.
- `5m` requires a same-direction BOS or CHOCH before a candidate can be
  accepted.

LONG and SHORT evaluation use mirrored rules. Both directions are evaluated
and persisted for every analysis snapshot, including suppressed candidates.

## Score

The score is reconstructed from seven persisted components totaling 100:

- higher-timeframe alignment: 20;
- liquidity sweep: 20;
- structure and displacement: 20;
- entry-zone quality: 15;
- premium or discount location: 10;
- volume and Open Interest: 10;
- funding and BTC condition: 5.

A score below 70 is suppressed. A score at least 85 is strong. Mandatory
conditions are independent from score: a high-scoring candidate remains
suppressed without 5m confirmation, a valid trade plan, or net reward-to-risk
of at least 1:3.

## Risk

- Reference balance: 10,000 USDT.
- Monetary risk: 1%, including estimated entry, stop-exit, and target fees.
- Quantity is rounded down to the Bybit instrument quantity step.
- Exchange minimum notional is enforced.
- Leverage is capped by the configured 20x display maximum, the instrument
  maximum, and the liquidation-buffer estimate.
- Leverage never increases quantity or monetary risk.
- TP1 defaults to 1.5R; TP2 uses the nearest directional liquidity target.

## Audit and startup safety

Every parameter set has an immutable version and SHA-256 checksum. Each
snapshot stores the exact timeframe cutoffs, market context, all four SMC
analyses, score components, evidence, warnings, and suppression reasons.

The worker waits for market-data refresh and backfill readiness before strategy
analysis. It pauses during reconnect or gap recovery. A unique signature over
symbol, strategy version, and candle cutoffs prevents duplicate snapshots.
