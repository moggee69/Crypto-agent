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

1. Put each bot's data files in the data dir (default `dashboard/data/`) as
   `{sw,sl,a4}_portfolio.json` / `_trades.csv` / `_equity.csv` — either pull the
   droplet's live `swing_*` files, or regenerate them with the scripts below.
2. `python build_dash.py` → rewrites `dashboard.html` with fresh data.
3. Re-publish `dashboard.html` as the Claude Artifact (same URL).

Regenerate the bot data files:
- `python sim_backfill.py` — deterministically replays 002 + 003 from the mid-July
  baseline (writes `sw_*` + `sl_*`); matches the running bots.
- `python pull_live_004.py` — pulls **004** live from the droplet (writes `a4_*`).
  Use this, NOT `agent004_baseline.py`: 004 has no deterministic backfill, so a local
  baseline pegs every buy price to today's close and its Open-Positions P&L shows ~0.
  (`agent004_baseline.py` / `agent004_bundle.py` remain only for seeding a brand-new
  004 baseline on the droplet, not for the dashboard.)

## Paths are portable — no edits needed

All scripts import **`_paths.py`**, which resolves everything relative to the repo:
it adds `../swing_bot` to `sys.path` and points `DASH_HTML` at `dashboard.html` and
`DATA_DIR` at `dashboard/data/`. So a fresh `git clone` runs as-is on any machine —
just install the deps (`requests`, `pyyaml`). To read the bots' data from somewhere
else, set the `DASH_DATA` environment variable to that folder. The generated
`data/` dir is git-ignored.
