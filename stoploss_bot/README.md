# Real-time Trailing Stop-Loss Bot

A long-running bot that watches a fixed crypto watchlist on Coinbase's live
price feed and protects positions with an **intraday trailing stop** — it reacts
in seconds, not once a day like the momentum bot in the parent folder.

**This is not financial advice. Most automated trading strategies lose money.
Only ever fund this with money you can afford to lose entirely.**

## How it works
The bot subscribes to Coinbase's public ticker websocket and, on every price
update:

- **Entry (buy the dip):** while flat and below the max-position count, it buys a
  coin once its price has fallen `entry.dip_pct` below the highest price seen in
  the last `entry.lookback_minutes`.
- **Exit (trailing stop):** while holding, it tracks the *peak* price since entry
  and sells the moment price drops `exit.trail_pct` below that peak. An optional
  `exit.take_profit_pct` can also force an exit. After a stop-out it waits
  `entry.rebuy_cooldown_minutes` before re-entering the same coin.

Unlike the daily bot, this is a **persistent process** — it must run on an
always-on host (a small VPS or a Pi), **not** GitHub Actions.

## Files
| File | Purpose |
|------|---------|
| `config.yaml`   | All settings (watchlist, capital, dip %, trail %, fees) |
| `feed.py`       | Coinbase public ticker websocket, with auto-reconnect |
| `strategy.py`   | Pure entry/exit decision functions |
| `portfolio.py`  | Paper/live execution + position & peak tracking, CSV logs |
| `bot.py`        | The long-running loop wiring the feed to the strategy |
| `deploy/stoploss-bot.service` | systemd unit for an always-on VPS |

## Step 1 — Install
```bash
cd stoploss_bot
python3 -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Step 2 — Run in DRY RUN (paper) — do this first, for weeks
`config.yaml` ships with `dry_run: true`. **No API keys are needed** — the price
feed is public.
```bash
python bot.py
```
It keeps a **simulated portfolio** (starting cash = `risk.total_capital_usd`)
in `paper_portfolio.json`, applies a simulated taker fee (`paper.fee_pct`), and
logs every trade to `trades_log.csv` plus periodic equity rows to
`equity_history.csv`. Leave it running and watch the heartbeat / P&L. To restart
the simulation cleanly, delete `paper_portfolio.json`, `equity_history.csv` and
`trades_log.csv`.

> The equity CSV uses the same columns as the parent bot, so you can reuse its
> `report.py` to chart this bot's curve: `python ../report.py` from this folder.

> On a fresh start the bot has no price history, so it won't buy immediately —
> it first needs to observe a local high and then a dip. Give it a few minutes.

## Step 3 — Tune the strategy
Defaults (edit in `config.yaml`):

| Setting | Default | Meaning |
|---------|---------|---------|
| `watchlist` | BTC, ETH, SOL (USD) | coins to watch & trade |
| `entry.dip_pct` | 3% | how far below the recent high to buy |
| `entry.lookback_minutes` | 60 | window for that "recent high" |
| `exit.trail_pct` | 5% | trailing-stop distance from the peak |
| `exit.take_profit_pct` | 0 (off) | optional hard take-profit |
| `risk.per_position_usd` | 150 | capital per entry (0 = capital / max_positions) |
| `risk.max_positions` | 3 | max coins held at once |
| `entry.rebuy_cooldown_minutes` | 30 | pause before re-buying after a stop |

## Step 4 — Go live (only after paper proves out)
1. Create Coinbase **Advanced Trade** API keys: **View + Trade only, NEVER
   Withdraw**. Restrict to your VPS IP if possible.
2. Export them as env vars on the host (never commit them, never paste in chat):
   ```bash
   export COINBASE_API_KEY="organizations/xxx/apiKeys/xxx"
   export COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----..."
   ```
3. Fund a small amount, set `risk.total_capital_usd` to match, set
   `dry_run: false`, and watch it closely for the first few days.

## Step 5 — Run always-on (VPS)
Use the included systemd unit (see comments at the top of
`deploy/stoploss-bot.service` for the install commands). It restarts the bot on
crash or reboot and appends logs to `bot.log` / `journalctl`.

## Risk controls built in
- `dry_run` default on — no accidental live trading
- Trailing stop on every position, evaluated on every tick
- `max_positions` cap and per-position sizing
- Post-stop re-entry cooldown to avoid churning in a falling market
- Websocket auto-reconnect + staleness watchdog so the feed can't silently die
- Trade-only API keys (no withdrawal rights)
- Full CSV audit trail of every decision

## Honest expectations
- A trailing stop caps losses but also sells into normal volatility — in choppy
  markets it will stop you out repeatedly and bleed fees. Tune `trail_pct` to the
  coin's volatility.
- Market sells pay Coinbase taker fees (~0.6% at small volume); each round trip
  costs ~1.2%. Frequent stops add up.
- Each sale is a taxable event in most countries; keep `trades_log.csv`.
- Paper results always look better than live (no slippage, perfect fills).
