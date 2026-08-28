# Dashboard tooling

The LCARS "Trading desk" dashboard and the scripts that build it. Published as a
private Claude Artifact (claude.ai/code/artifacts); this is the source + pipeline.

## Files

- **dashboard.html** — the dashboard itself (self-contained: LCARS CSS + render JS +
  an embedded `const DATA={...}` snapshot). Publish it as a Claude Artifact. It is
  the persistent source `build_dash.py` reads and rewrites.
- **build_dash.py** — rebuilds `DATA` from the bots' live files + live Coinbase
  candles, then re-injects it into `dashboard.html`. Reads each bot's
  `{tag}_portfolio.json` / `{tag}_trades.csv` / `{tag}_equity.csv` (sw = Agent 002,
  sl = Agent 003, a4 = Agent 004). Builds both signal radars, the 16-coin buy&hold
  benchmark, and the candle panel.
- **sim_backfill.py** — replays the MACD/RSI strategy from the mid-July baseline to
  now for Agents 002/003, writing each bot's trade log + equity log + end state
  (the "treat July as live" backfill deployed to the bots).
- **baseline_reset.py / baseline_reset_a3.py** — seed Agents 002 / 003 to hold their
  watchlist bought at the 2026-07-15 close (peak/armed correctly initialised).
- **agent004_baseline.py / agent004_bundle.py** — seed Agent 004 (Top-8) with a
  *today* baseline (no survivorship backfill) and generate its trade/equity records.

## How the dashboard updates

1. (optional) pull each bot's live `swing_portfolio.json` / `swing_trades.csv` /
   `swing_equity.csv` from the droplet into the working dir as `{sw,sl,a4}_*`.
2. `python build_dash.py` → rewrites `dashboard.html` with fresh data.
3. Re-publish `dashboard.html` as the Claude Artifact (same URL).

## ⚠️ Paths are machine-specific

These scripts have **absolute paths hardcoded** to the machine they were written on
(a temp scratchpad dir + the local repo path). Before running them on another
machine, update the `OUT` / `SC` / `sys.path` constants near the top of each script
to point at wherever you keep `dashboard.html` and the bots' data files. The logic
is portable; only the paths need changing.
