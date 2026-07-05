# Swing Bot — dip-buy + trend-break exit

An automated paper bot that accumulates your coins on real dips, rides the runs,
and steps out when momentum rolls over. Built for someone who **can't watch the
market all day** — it checks the daily/4-hour candles every 15 minutes and acts
on your rules, hands-off.

**Not financial advice. Paper/dry-run by default. Prove it before funding it.**

## The strategy
- **Buy:** wait for a real dip — **2–4 red daily candles** — then buy on the
  **green turn** (momentum swinging back up). If the fall keeps going past 4
  days, it drops to the **4-hour chart** and buys the first green 4h candle
  (catches the bottom of a longer fall without waiting for a full green day).
- **Insurance (optional, on by default):** only buy when the coin is in a
  **broader uptrend** (price above its 30-day average). This stops it buying
  into a confirmed downtrend — the one scenario that hurt it most in backtests.
- **Sell:** exit when the daily close drops **below its 7-day average** — a
  *trend break*, not a fixed target. This rides runs and only steps out when the
  trend actually cracks. (Backtested far better than a % trailing stop.)
- One equal cash bucket per coin; each trades independently.

## Honest expectations (read this)
Backtests were clear about *when* this works:
- **In a rising market (recovery/bull), it beat buy-and-hold** — it captures the
  runs and sidesteps the pullbacks.
- **In a sustained bear, it loses** — dip-buying keeps pulling it back in. The
  insurance filter softens this (cut a full-cycle loss roughly in half) but
  doesn't eliminate it.

So this is a **recovery/bull strategy**, and it's a bet that the market is
turning up. That's exactly why it runs on **paper first** — so you can watch it
prove (or disprove) itself in real time, at zero risk, before any real money.

## Files
| File | Purpose |
|------|---------|
| `config.yaml` | Watchlist, capital, buy/sell rules, insurance toggle |
| `data.py` | Fetch completed daily/4h candles from Coinbase (public, no key) |
| `strategy.py` | Pure decision helpers (red-run, moving averages, filters) |
| `portfolio.py` | Per-coin paper accounting, trade + equity logs |
| `bot.py` | The polling loop (checks candles every 15 min) |
| `deploy/swing-bot.service` | systemd unit for always-on running |

## Run it (paper)
```bash
cd swing_bot
python3 -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```
It prints a startup banner, then a heartbeat each cycle. Trades go to
`swing_trades.csv`, equity to `swing_equity.csv`, state to `swing_portfolio.json`.
No API keys needed — it uses Coinbase's public candle feed.

## Always-on (VPS)
See `deploy/swing-bot.service`. It runs under systemd alongside the stop-loss
bot, restarts on crash/reboot, and logs to `bot.log` / `journalctl`.

## Tuning
- `exit.ma_exit_days` — the trend-break sensitivity (7 = backtest sweet spot;
  higher = holds longer, lower = exits quicker).
- `entry.insurance_uptrend_filter.enabled` — turn the downtrend guard off to buy
  every qualifying dip regardless of the bigger trend (more aggressive).
- `entry.min/max_red_candles`, `extended_fall_days` — the dip definition.

## Going live (only after paper proves out)
Add Coinbase Advanced Trade keys (View + Trade, never Withdraw) and wire real
order execution — the current build is **paper only** by design. Don't rush it.
