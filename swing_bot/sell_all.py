"""EMERGENCY LIQUIDATION — market-sell every live-bot holding (002 + 003) to USDC.

    *** THIS SELLS REAL MONEY. Claude does NOT run the live path — the user does. ***

The bull-top "sell all" tool: converts all real crypto held by the two live bots
into a single bag of USDC, fast. Reads the TRUE account balances (not just the bots'
tracked qty). One engine, used by both the CLI and the app's Sell-All button.

FOUR gates — ALL required before a single order is placed:
  1. --execute flag       (omit it = DRY RUN: prints the plan, sells NOTHING)
  2. passcode             (must match /opt/crypto-agent/sell_passcode.txt)
  3. typed confirmation   (CLI: type SELL ALL  |  app: the on-screen confirm step)
  4. live API keys        (COINBASE_API_KEY + secret; droplet only)

A stray bot HALT file does NOT block this (a deliberate liquidation must go through).

Modes:
    python sell_all.py                       # DRY RUN, human-readable plan
    python sell_all.py --json                # DRY RUN, JSON (used by the app preview)
    python sell_all.py --execute             # REAL, interactive: prompts passcode + SELL ALL
    python sell_all.py --execute --web --json # REAL, non-interactive: passcode via
                                             #   SELL_ALL_PASSCODE env (the app path)
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
BOTS = [os.path.join(ROOT, "swing_bot", "swing_portfolio.json"),
        os.path.join(ROOT, "agent003", "swing_portfolio.json")]

os.environ["BOT_KILL_SWITCH"] = "/nonexistent/sell_all_never_halts"  # set before broker import


def load_env(path):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def held_products():
    out = set()
    for pf in BOTS:
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


def make_broker():
    load_env(ENV_FILE)
    sys.path.insert(0, os.path.join(ROOT, "swing_bot"))
    import broker
    live = yaml.safe_load(open(os.path.join(ROOT, "swing_bot", "config.yaml"))).get("live", {})
    return broker, broker.Broker({"live": live})


def build_plan(broker, b):
    """[{coin, prod, qty, price, value, ok}], usdc_now — from true account balances."""
    accts = b._retry(lambda: b.client.get_accounts())
    bal = {}
    for a in (broker._field(accts, "accounts") or []):
        cur = broker._field(a, "currency")
        v = broker._num(broker._field(broker._field(a, "available_balance"), "value"))
        if v > 0:
            bal[cur] = v
    plan = []
    for prod in sorted(held_products()):
        base = prod.replace("-USD", "")
        m = b.meta(prod)
        qty = b._floor_to(bal.get(base, 0.0), m["base_inc"])
        px = spot(prod)
        plan.append({"coin": base, "prod": prod, "qty": qty, "price": px,
                     "value": round(qty * px, 2), "ok": qty > 0 and qty >= (m["base_min"] or 0)})
    return plan, bal.get("USDC", 0.0)


def main():
    args = set(sys.argv[1:])
    as_json = "--json" in args
    execute = "--execute" in args
    web = "--web" in args

    broker, b = make_broker()
    plan, usdc = build_plan(broker, b)
    sellable = [p for p in plan if p["ok"]]
    est = round(sum(p["value"] for p in sellable), 2)

    if not execute:
        result = {"mode": "preview", "coins": plan, "count": len(sellable),
                  "est_proceeds": est, "usdc_before": round(usdc, 2),
                  "usdc_after_est": round(usdc + est, 2)}
        if as_json:
            print(json.dumps(result))
            return
        print("\n=== LIQUIDATION PLAN — sell all live-bot holdings to USDC ===\n")
        for p in plan:
            print(f"  {p['coin']:<8}{p['qty']:>18.8g}{p['price']:>13.6g}{p['value']:>13.2f}"
                  f"   {'SELL' if p['ok'] else 'skip (dust/below min)'}")
        print(f"\n  coins to sell: {len(sellable)}   est. proceeds ~${est:,.2f}")
        print(f"  USDC now ${usdc:,.2f}  ->  est. after ~${usdc + est:,.2f}")
        print("\n*** DRY RUN — nothing was sold. Add --execute to sell for real. ***\n")
        return

    # ---- gates for real execution ----
    try:
        expected = open(PASSCODE_FILE).read().strip()
    except OSError:
        expected = ""
    if not expected:
        msg = "passcode not set on server"
        print(json.dumps({"ok": False, "error": msg}) if as_json else f"ABORT: {msg}")
        return
    if web:
        if os.environ.get("SELL_ALL_PASSCODE", "") != expected:
            print(json.dumps({"ok": False, "error": "bad passcode"}) if as_json else "ABORT: bad passcode")
            return
    else:
        if getpass.getpass("  Passcode: ").strip() != expected:
            print("ABORT: wrong passcode"); return
        if input('  Type "SELL ALL" to confirm: ').strip() != "SELL ALL":
            print("ABORT: not confirmed"); return

    results, proceeds = [], 0.0
    for p in sellable:
        try:
            f = b.market_sell(p["prod"], p["qty"])
            proceeds += f.quote_spent
            results.append({"coin": p["coin"], "qty": f.qty, "price": f.price,
                            "usd": round(f.quote_spent, 2), "fee": round(f.fee, 2)})
            if not as_json:
                print(f"  SOLD {p['coin']:<8} {f.qty:g} @ ${f.price:,.6g} -> ${f.quote_spent:,.2f}")
        except Exception as e:   # noqa: BLE001
            results.append({"coin": p["coin"], "error": str(e)})
            if not as_json:
                print(f"  FAILED {p['coin']}: {e}")
    usdc_after = b.available("USDC")
    out = {"ok": True, "mode": "executed", "sold": results,
           "proceeds": round(proceeds, 2), "usdc_after": round(usdc_after, 2)}
    print(json.dumps(out) if as_json else
          f"\n  Done. Proceeds ~${proceeds:,.2f}.  USDC now ${usdc_after:,.2f}. "
          f"Reconcile/reset the bots before restarting.\n")


if __name__ == "__main__":
    main()
