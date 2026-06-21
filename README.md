# AI Crypto Trading Agent — Setup Guide

A daily momentum-rotation bot for the top 20 cryptos, trading against USDC on Coinbase, with an optional Claude AI risk filter and a mandatory dry-run mode.

**This is not financial advice. Most automated trading strategies lose money. Only ever fund this with money you can afford to lose entirely.**

## How it works
Once a day the agent: pulls the top 20 coins by market cap, scores each by blended momentum (24h/7d/30d returns), holds the strongest 5 in equal weights, keeps a 20% USDC reserve at all times, and rotates out of anything that loses momentum. If everything is falling, it sits 100% in USDC. Optionally, Claude reviews every planned buy and can veto coins with serious red flags.

## Step 1 — Install (5 min)
```bash
cd crypto-agent
python3 -m venv venv && source venv/bin/activate
pip install requests pyyaml coinbase-advanced-py anthropic
```

## Step 2 — Run in DRY RUN mode (do this for 2–4 weeks)
`config.yaml` ships with `dry_run: true`. No API keys needed yet:
```bash
python main.py
```
In dry-run the bot keeps a **simulated portfolio** — starting cash equal to
`risk.total_capital_usdc`, persisted between runs in `paper_portfolio.json`.
Each run it values your holdings at live prices, applies a simulated taker fee
(`paper.fee_pct`), and prints a **Paper P&L** summary vs. your starting capital.
Every intended trade is also appended to `trades_log.csv` as an audit trail.

Run it once a day for a few weeks and watch the paper P&L. If the strategy
loses money on paper, it will lose money live. To restart the simulation from a
clean slate, delete `paper_portfolio.json` **and** `equity_history.csv`.

Each run also appends a snapshot to `equity_history.csv`. To see your track
record as a chart and summary stats (return, peak, max drawdown, fees) any time:
```bash
python report.py
```

> Note: the simulation assumes it owns nothing until the bot buys it. If you
> already hold coins in real life, the paper portfolio won't reflect that —
> it's measuring *this strategy's* decisions in isolation, which is what you
> want for evaluating it.

## Step 3 — Create Coinbase API keys (when ready for live)
1. Go to **portfolio.coinbase.com** → Settings → API → Create API key
   (Coinbase Advanced Trade, not the old Coinbase Pro)
2. Permissions: enable **View** and **Trade** only. NEVER enable **Transfer/Withdraw** — if the key leaks, attackers still can't withdraw your funds.
3. Restrict the key to your server's IP address if possible.
4. Store keys as environment variables, never in the code:
```bash
export COINBASE_API_KEY="organizations/xxx/apiKeys/xxx"
export COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----..."
```

## Step 4 — Go live (small)
1. Move a small amount of USDC into Coinbase (e.g., $100–500).
2. Set `total_capital_usdc` in config.yaml to match.
3. Set `dry_run: false`.
4. Run `python main.py` manually for the first few days and watch it.

## Step 5 — Schedule it daily
On any Linux server / Raspberry Pi / always-on machine:
```bash
crontab -e
# Run daily at 00:15 UTC:
15 0 * * * cd /path/to/crypto-agent && ./venv/bin/python main.py >> agent.log 2>&1
```
Cloud option: a $5/month VPS (DigitalOcean, Hetzner) works fine.

## Step 6 — Optional: enable the Claude AI risk filter
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```
Then set `ai_analyst.enabled: true` in config.yaml. Claude vets each buy for red flags (exploits, delistings, regulatory action) and can veto it. Note: the AI does **not** predict prices — nothing can do that reliably.

## What about MetaMask?
MetaMask is a self-custody wallet, not an exchange — it has no trading API. Automating trades from it means signing transactions on a DEX (Uniswap etc.) via web3, which adds gas fees, slippage, smart-contract risk, and private-key exposure. For daily rotation of top-20 majors, Coinbase is cheaper and far safer. Keep MetaMask for long-term storage: periodically withdraw profits from Coinbase to it.

## Risk controls built in
- `dry_run` default on — no accidental live trading
- 20% permanent USDC reserve
- 25% max per coin
- `daily_loss_limit_pct` circuit breaker — halts all trading for a run if equity
  has fallen by that % or more since the previous run (holds, forces review)
- Goes fully to cash when momentum is negative market-wide
- Trade-only API keys (no withdrawal rights)
- Full CSV audit trail of every decision

## Honest expectations
- Momentum strategies do well in trends and get chopped up in sideways markets.
- Daily rebalancing incurs Coinbase taker fees (~0.6% at small volume) — fees compound and matter.
- Each sale is a taxable event in most countries; keep `trades_log.csv` for your accountant.
- Backtest results and dry-run results always look better than live results.
