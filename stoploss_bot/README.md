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
| `watchlist` | XLM, HBAR, XRP, AVAX, LINK, ONDO, FLR, HYPE (USD) | coins to watch & trade |
| `entry.dip_pct` | 3% | how far below the recent high to buy |
| `entry.lookback_minutes` | 60 | window for that "recent high" |
| `exit.trail_pct` | 5% | trailing-stop distance from the peak |
| `exit.take_profit_pct` | 0 (off) | optional hard take-profit |
| `risk.per_position_usd` | 150 | capital per entry (0 = capital / max_positions) |
| `risk.max_positions` | 3 | max coins held at once |
| `entry.rebuy_cooldown_minutes` | 30 | pause before re-buying after a stop |

## Step 4 — Go live (only after paper proves out)

> **Going live is a deliberate, supervised step — not a switch you flip and walk
> away from.** Once live, the bot places real market orders on Coinbase by
> itself: it buys on a dip and sells on the trailing stop. You'll see those
> trades in the Coinbase app, but the app shows *what* happened — the bot's
> `bot.log` / `trades_log.csv` show *why* and what it's currently holding. Treat
> the first few weeks as "running with supervision," and watch the bot's logs,
> not just the app.

### Pre-flight checklist
- [ ] **Paper first.** Prove it actually makes money on paper for several weeks
      (`equity_history.csv`). Paper always looks better than live.
- [ ] **API keys:** create Coinbase **Advanced Trade** keys with **View + Trade
      only — NEVER Withdraw**. Restrict to the VPS IP if possible. If a key
      leaks, attackers still can't move your funds.
- [ ] **Store keys on the host, never in the repo.** Use a root-only env file
      read by systemd (see below) — don't `git commit` them, don't paste them
      into chat.
- [ ] **Fund in the right currency.** The watchlist uses `-USD` pairs, so the
      account needs **USD** balance (not USDC) to buy with.
- [ ] **Match the capital.** Set `risk.total_capital_usd` to what you actually
      funded, and sanity-check `per_position_usd × max_positions` ≤ that.
- [ ] **Start small** — a few hundred dollars you can afford to lose entirely.
- [ ] **Set `dry_run: false`** and restart the service.
- [ ] **Watch the first trades by hand** before trusting it unattended.

### Wiring keys into the service (live)
Create `/etc/stoploss-bot.env` (root-only) on the host:
```bash
COINBASE_API_KEY=organizations/xxx/apiKeys/xxx
COINBASE_API_SECRET=-----BEGIN EC PRIVATE KEY-----...
```
```bash
chmod 600 /etc/stoploss-bot.env
```
Add this line under `[Service]` in `/etc/systemd/system/stoploss-bot.service`,
then `systemctl daemon-reload && systemctl restart stoploss-bot`:
```ini
EnvironmentFile=/etc/stoploss-bot.env
```

### Know these live-mode limitations before you rely on it
- **The stop-loss lives in the bot, not on Coinbase.** If the Droplet or the feed
  goes down while you hold a coin, nothing is watching it — you're unprotected
  until it's back. Consider also placing a real stop order on Coinbase as a
  backstop.
- **The bot only manages positions *it* opened** — it tracks them in its own
  state file, not by reading your Coinbase balance. **Don't manually trade the
  watchlist coins in the app**, or the bot's view goes out of sync.
- **No take-profit and no daily circuit-breaker by default.** A 5% trailing stop
  on volatile coins will stop out often; each round trip costs ~1.2% in fees.
- **The live execution path is intentionally simple** (estimates fill qty from
  the last price). Review it and start with a small amount before scaling up.

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
