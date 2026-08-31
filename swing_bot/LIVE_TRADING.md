# Live trading — operations guide

The bots run **paper** by default. Real orders only happen when every gate below
is open, per bot. Read this before enabling anything.

## The three gates (all required)

A bot places real orders only when **all** of these are true:

1. `dry_run: false` in its `config.yaml`
2. `live.live_trading: true` in its `config.yaml`
3. `COINBASE_API_KEY` and `COINBASE_API_SECRET` present in its environment

Any one missing → the bot stays on paper. On top of that, a **kill-switch** file
freezes live orders instantly regardless of the gates.

## Kill-switch

Create a file named `HALT` in the bot's working dir to freeze all live orders for
that bot immediately; delete it to resume:

```bash
touch /opt/crypto-agent/agent003/HALT     # freeze Blue-chip 003
rm    /opt/crypto-agent/agent003/HALT      # resume
```

(Override the filename with the `BOT_KILL_SWITCH` env var if you want a shared one.)

## One-time setup on the droplet

1. Install the SDK into the shared venv (paper mode never needed it):
   ```bash
   /opt/crypto-agent/swing_bot/venv/bin/pip install coinbase-advanced-py
   ```
2. Put the keys where **only** the service reads them — never in git. Use a
   systemd `EnvironmentFile` with `600` perms:
   ```bash
   umask 077
   cat > /etc/crypto-agent-<bot>.env <<'EOF'
   COINBASE_API_KEY=<paste the API key id>
   COINBASE_API_SECRET=<paste the secret / private key>
   EOF
   ```
   Then in the bot's `.service` unit add:  `EnvironmentFile=/etc/crypto-agent-<bot>.env`

## Smoke test — do this FIRST, before enabling any bot

With the keys exported in your shell (not the service), place one tiny real order
and sell it back to prove the whole path:

```bash
cd /opt/crypto-agent/swing_bot
COINBASE_API_KEY=... COINBASE_API_SECRET=... \
  venv/bin/python live_smoke_test.py BTC-USD 2 --yes
```

It prints the product minimums, the actual fill (size/price/fee) for both sides,
and the round-trip cost (a few cents). If it says `SMOKE TEST PASSED`, the
execution layer works against your account.

## Enabling a bot (the canary)

Only after the smoke test passes:

1. In that bot's `config.yaml`: set `dry_run: false` and `live.live_trading: true`.
   Set `live.max_order_usd` to a sane ceiling (a single order can never exceed it).
2. Set `capital.total_usd` to your real starting amount.
3. Add the `EnvironmentFile=` line to its `.service`, then:
   ```bash
   systemctl daemon-reload && systemctl restart <bot>
   journalctl -u <bot> -n 30 -f
   ```
   The banner must read **`LIVE TRADING — REAL MONEY`**. If it says the gate is
   closed, one of the three gates isn't set.

## What happens at startup (live)

The bot **reconciles** to the real account: the exchange is the source of truth for
coin holdings, so an unrecorded fill (e.g. after a mid-order crash) is adopted, and
a position that's gone off-exchange is marked flat. Per-coin USD cash buckets stay
bot-internal.

## Safety notes

- Keys must be **View + Trade only, never Withdraw** — a leaked key then can't move
  funds off Coinbase.
- Every order carries an idempotent `client_order_id`, so a retry after a network
  blip can't double-order.
- Orders are capped by `live.max_order_usd`; sells never exceed the real balance.
- A failed order is skipped (and pushed to ntfy), not retried into a bad state.
- To revert a bot to paper: set `live_trading: false` (or `dry_run: true`) and
  restart. Drop a `HALT` file for an instant freeze without a restart.
