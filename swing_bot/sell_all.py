"""EMERGENCY LIQUIDATION — market-sell every live-bot holding (002 + 003) to USDC.

    *** THIS SELLS REAL MONEY. Claude does NOT run the live path — the user does. ***

The bull-top "sell all" tool: converts all real crypto held by the two live bots
into a single bag of USDC, fast, in one command. Reads the TRUE account balances
(not just the bots' tracked qty) so it liquidates what's actually there.

FOUR gates — ALL required before a single order is placed:
  1. --execute flag       (omit it = DRY RUN: prints the plan, sells NOTHING)
  2. passcode             (prompted; must match /opt/crypto-agent/sell_passcode.txt)
  3. typed confirmation   (must type exactly:  SELL ALL)
  4. live API keys        (COINBASE_API_KEY + secret; droplet only — Broker fails otherwise)

A stray bot HALT file does NOT block this (a deliberate liquidation must go through).

Usage (on the droplet, from swing_bot/):
    python sell_all.py                 # DRY RUN — show the liquidation plan, sell nothing
    python sell_all.py --execute       # REAL — prompts passcode + confirmation, then sells
"""
import getpass
import json
import os
import sys

import requests
import yaml

ROOT = "/opt/crypto-agent"
ENV_FILE = "/etc/crypto-agent-003.env"
PASSCODE_FILE = os.path.join(ROOT, "sell_passcode.txt")
BOTS = [("002 Utility", os.path.join(ROOT, "swing_bot", "swing_portfolio.json")),
        ("003 Blue-chip", os.path.join(ROOT, "agent003", "swing_portfolio.json"))]

# Never let a stray bot HALT file veto a deliberate liquidation (set before broker import).
os.environ["BOT_KILL_SWITCH"] = "/nonexistent/sell_all_never_halts"


def load_env(path):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def held_products():
    """Products the two live bots currently hold (e.g. {'XLM-USD', 'BTC-USD', ...})."""
    out = set()
    for _, pf in BOTS:
        try:
            p = json.load(open(pf))
        except OSError:
            continue
        for prod, c in p.get("coins", {}).items():
            if c.get("holding") and c.get("qty", 0) > 0:
                out.add(prod)
    return out


def spot(prod):
    try:
        return float(requests.get(f"https://api.exchange.coinbase.com/products/{prod}/ticker",
                                  timeout=10).json()["price"])
    except Exception:
        return 0.0


def main():
    execute = "--execute" in sys.argv
    load_env(ENV_FILE)
    sys.path.insert(0, os.path.join(ROOT, "swing_bot"))
    import broker

    live = yaml.safe_load(open(os.path.join(ROOT, "swing_bot", "config.yaml"))).get("live", {})
    b = broker.Broker({"live": live})

    # one snapshot of every account balance (ground truth), not the bots' tracked qty
    accts = b._retry(lambda: b.client.get_accounts())
    bal = {}
    for a in (broker._field(accts, "accounts") or []):
        cur = broker._field(a, "currency")
        v = broker._num(broker._field(broker._field(a, "available_balance"), "value"))
        if v > 0:
            bal[cur] = v

    held = held_products()
    print("\n=== LIQUIDATION PLAN — sell all live-bot holdings to USDC ===\n")
    print(f"  {'coin':<8}{'sellable qty':>18}{'~price':>13}{'~USD value':>13}   status")
    plan, total = [], 0.0
    for prod in sorted(held):
        base = prod.replace("-USD", "")
        m = b.meta(prod)
        qty = b._floor_to(bal.get(base, 0.0), m["base_inc"])
        px = spot(prod)
        val = qty * px
        ok = qty > 0 and qty >= (m["base_min"] or 0)
        plan.append((prod, base, qty, ok))
        if ok:
            total += val
        print(f"  {base:<8}{qty:>18.8g}{px:>13.6g}{val:>13.2f}   {'SELL' if ok else 'skip (dust/below min)'}")

    other = {c: v for c, v in bal.items()
             if c != "USDC" and (c + "-USD") not in held and v > 0}
    if other:
        print("\n  NOTE — other non-USDC balances NOT in this plan (left untouched):")
        for c, v in other.items():
            print(f"    {c}: {v:g}")

    usdc = bal.get("USDC", 0.0)
    n = sum(1 for *_, ok in plan if ok)
    print(f"\n  coins to sell: {n}    est. proceeds ~${total:,.2f}")
    print(f"  USDC now: ${usdc:,.2f}   ->   est. after liquidation: ~${usdc + total:,.2f}")

    if not execute:
        print("\n*** DRY RUN — nothing was sold. Re-run with --execute to sell for real. ***\n")
        return

    print("\n" + "=" * 62)
    print("  LIVE EXECUTION — this places REAL market-sell orders on Coinbase.")
    print("=" * 62)
    try:
        expected = open(PASSCODE_FILE).read().strip()
    except OSError:
        print(f"  ABORT: passcode file {PASSCODE_FILE} is missing.")
        return
    if not expected:
        print("  ABORT: passcode not set.")
        return
    if getpass.getpass("  Passcode: ").strip() != expected:
        print("  ABORT: wrong passcode.")
        return
    if input('  Type "SELL ALL" to confirm: ').strip() != "SELL ALL":
        print("  ABORT: not confirmed.")
        return

    print("\n  Liquidating...\n")
    proceeds = 0.0
    for prod, base, qty, ok in plan:
        if not ok:
            continue
        try:
            f = b.market_sell(prod, qty)
            proceeds += f.quote_spent
            print(f"  SOLD {base:<8} {f.qty:g} @ ${f.price:,.6g}  ->  ${f.quote_spent:,.2f}  (fee ${f.fee:.2f})")
        except Exception as e:   # noqa: BLE001
            print(f"  FAILED {base}: {e}")
    print(f"\n  Done. Proceeds ~${proceeds:,.2f}.   USDC now: ${b.available('USDC'):,.2f}")
    print("  (The bots still think they hold these — reconcile or reset their state before restarting.)\n")


if __name__ == "__main__":
    main()
