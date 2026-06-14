# SMC Core v1

`smc_core` is a synchronous, deterministic domain library. It has no database,
network, web-framework, exchange-adapter, Telegram, or event-loop dependency.
The same ordered closed candles and `SMCConfig` always produce the same
`SMCAnalysis`.

## Candle policy

- Input candles must share one symbol and timeframe.
- Candles must be strictly ordered by exchange `open_time`.
- Every timestamp is timezone-aware.
- Only closed candles may be passed to the library.

## Statistics

- True Range is the maximum of candle range, distance from the previous close
  to the high, and distance from the previous close to the low.
- ATR uses Wilder smoothing. The first value is the arithmetic mean of the
  first configured period; earlier values are unavailable.
- Rolling range averages are simple arithmetic means.

## Swings and structure

- A Swing High is strictly higher than every high in its configured left and
  right lookback windows.
- A Swing Low is strictly lower than every low in both windows.
- Equal neighboring extrema are not strict swings.
- A swing becomes available only after its full right window has closed.
- A bullish break requires a close strictly above the latest available,
  unbroken Swing High. A bearish break mirrors this rule.
- A break in the current or neutral direction is BOS. The first break against
  the current direction is CHOCH.

## Liquidity

- A high sweep trades strictly above an available Swing High and closes
  strictly below it. It is a bearish sweep.
- A low sweep trades strictly below an available Swing Low and closes strictly
  above it. It is a bullish sweep.
- Equal Highs/Lows compare consecutive same-kind swings. Their price distance
  must be less than or equal to ATR multiplied by the configured tolerance and
  their bar separation must remain within the configured maximum.

## Displacement and zones

- Displacement requires candle body size at or above the configured ATR ratio,
  total range at or above the configured rolling-range ratio, and a close in
  the configured fraction near the directional extreme.
- A bullish FVG exists when the third candle low is strictly above the first
  candle high. A bearish FVG is mirrored. Gap size must be at least the
  configured ATR ratio.
- FVG lifecycle is `open`, `partially_filled`, then `filled`; wick penetration
  determines partial and complete filling.
- An Order Block is the last opposite-direction candle within the configured
  search window before a same-direction displacement candle that confirms BOS
  or CHOCH.
- The initial Order Block zone uses the source candle's full high-low range.
  A bullish block is invalidated by a close below its low; a bearish block is
  invalidated by a close above its high.

## Dealing range

The active dealing range uses the latest confirmed Swing High and Swing Low.
Its arithmetic midpoint separates discount and premium. A close exactly at the
midpoint is equilibrium.

## CPU isolation

Single bounded analyses are pure synchronous calls. Application workers submit
larger symbol batches through `AnalysisProcessPool`, which has bounded workers
and bounded pending batches. Run `python scripts/profile_smc_core.py` to profile
the current implementation on a deterministic 10,000-candle fixture.
