"""RESET & REACTIVATE — the mirror of sell_all.py. Re-arms the two live bots (002 +
003) after a bull-top cash-out: resets each to a fresh all-cash baseline at a chosen
capital, then restarts them so they seed-buy every coin at CURRENT (bottom) prices
and resume normal management.

    *** THE RESTART PLACES REAL BUY ORDERS. Claude does NOT run the live path. ***

What it does NOT touch: config.yaml and bench_basis.json (git-tracked). Capital is
written straight into the fresh portfolio JSON, so no tracked file changes.
After seeding, the buy & hold benchmark should be regenerated from a dev machine
(ask Claude: "refresh the benchmark") so the dashboards reset to the new cycle.

FOUR gates for a real reset — ALL required:
  1. --execute flag       (omit = DRY RUN: prints the plan, changes NOTHING)
  2. passcode             (must match /opt/crypto-agent/sell_passcode.txt)
  3. typed confirmation   (type: REACTIVATE)
  4. enough free USDC      (>= total to deploy, unless --force)

Usage (on the droplet, from swing_bot/):
    python reactivate.py                 # DRY RUN, per-bot capital = half your USDC
    python reactivate.py --per 400       # DRY RUN, $400 per bot
    python reactivate.py --per 400 --execute    # REAL: passcode + REACTIVATE, then reset+restart
"""
import getpass
import json
import math
import os
import subprocess
import sys
import time

import yaml

ROOT = "/opt/crypto-agent"
ENV_FILE = "/etc/crypto-agent-003.env"
PASSCODE_FILE = os.path.join(ROOT, "sell_passcode.txt")
# (label, dir, systemd service)
BOTS = [("002 Utility", os.path.join(ROOT, "swing_bot"), "swing-bot"),
        ("003 Blue-chip", os.path.join(ROOT, "agent003"), "agent003-bot")]

os.environ["BOT_KILL_SWITCH"] = "/nonexistent/reactivate_never_halts"


def load_env(path):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def bot_cfg(botdir):
    return yaml.safe_load(open(os.path.join(botdir, "config.yaml")))


def fresh_state(per, coins):
    n = len(coins)
    bucket = per / n
    return {"start_capital": per, "per_coin": bucket, "peak_equity": per, "fees_paid": 0.0,
            "coins": {p: {"cash": bucket, "holding": False, "qty": 0.0, "buy_price": 0.0,
                          "cost_usd": 0.0, "peak_price": 0.0, "armed": False,
                          "last_daily_ts": 0, "last_4h_ts": 0} for p in coins}}


def usdc_balance():
    load_env(ENV_FILE)
    sys.path.insert(0, os.path.join(ROOT, "swing_bot"))
    import broker
    live = bot_cfg(os.path.join(ROOT, "swing_bot")).get("live", {})
    return broker.Broker({"live": live}).available("USDC")


def main():
    args = sys.argv[1:]
    execute = "--execute" in args
    force = "--force" in args
    web = "--web" in args
    per = None
    if "--per" in args:
        per = float(args[args.index("--per") + 1])

    usdc = usdc_balance()
    if per is None:
        per = float(math.floor(usdc / 2))          # default: split free USDC across the two bots

    plans = []
    for label, botdir, svc in BOTS:
        cfg = bot_cfg(botdir)
        coins = cfg["watchlist"]
        seed = cfg.get("live", {}).get("seed_baseline", False)
        live_on = (not cfg.get("dry_run", True)) and cfg.get("live", {}).get("live_trading", False)
        plans.append({"label": label, "dir": botdir, "svc": svc, "coins": coins,
                      "n": len(coins), "seed": seed, "live": live_on,
                      "state": os.path.join(botdir, cfg.get("state_file", "swing_portfolio.json"))})

    total = per * len(plans)
    print("\n=== RESET & REACTIVATE PLAN ===\n")
    print(f"  free USDC now:        ${usdc:,.2f}")
    print(f"  capital per bot:      ${per:,.2f}   ({len(plans)} bots -> deploy ${total:,.2f})")
    print(f"  USDC left as dry powder: ${usdc - total:,.2f}\n")
    ok = True
    for p in plans:
        warn = ""
        if not p["seed"]:
            warn += "  !! seed_baseline is OFF in config — it will NOT re-buy"; ok = False
        if not p["live"]:
            warn += "  !! bot is not live in config"; ok = False
        print(f"  {p['label']:<14} reset to ${per:,.2f} across {p['n']} coins "
              f"(${per / p['n']:,.2f}/coin), restart {p['svc']}{warn}")
    if usdc < total:
        print(f"\n  !! NOT ENOUGH USDC: need ${total:,.2f}, have ${usdc:,.2f}"
              f"{' (override with --force)' if not force else ''}")
        ok = ok and force
    print("\n  On restart each bot reconciles flat, then market-buys every bucket at the\n"
          "  current price (REAL orders), then resumes normal swing management.")

    if not execute:
        print("\n*** DRY RUN — nothing changed. Add --execute to reset + restart for real. ***\n")
        return
    if not ok:
        print("\n  ABORT: plan has blocking warnings (see !! above).\n")
        return

    # ---- gates ----
    try:
        expected = open(PASSCODE_FILE).read().strip()
    except OSError:
        expected = ""
    if not expected:
        print("  ABORT: passcode not set."); return
    if web:
        if os.environ.get("REACTIVATE_PASSCODE", "") != expected:
            print("  ABORT: bad passcode."); return
    else:
        if getpass.getpass("  Passcode: ").strip() != expected:
            print("  ABORT: wrong passcode."); return
        if input('  Type "REACTIVATE" to confirm: ').strip() != "REACTIVATE":
            print("  ABORT: not confirmed."); return

    ts = int(time.time())
    print("\n  Resetting state...")
    for p in plans:
        d = p["dir"]
        for fn in ("swing_portfolio.json", "swing_equity.csv", "swing_trades.csv"):
            fp = os.path.join(d, fn)
            if os.path.exists(fp):
                os.rename(fp, f"{fp}.pre_reactivate.{ts}")     # archive (also frees fresh logs)
        json.dump(fresh_state(per, p["coins"]), open(p["state"], "w"), indent=2)
        halt = os.path.join(d, "HALT")
        if os.path.exists(halt):
            os.remove(halt)
        print(f"    {p['label']}: fresh ${per:,.2f} baseline written, logs archived, HALT cleared")

    print("\n  Restarting bots (this triggers the baseline seed-buys)...")
    subprocess.run(["systemctl", "restart"] + [p["svc"] for p in plans], check=False)

    print("  Waiting for baseline seed to complete...")
    deadline = time.time() + 180
    done = set()
    while time.time() < deadline and len(done) < len(plans):
        time.sleep(5)
        for p in plans:
            if p["label"] in done:
                continue
            try:
                st = json.load(open(p["state"]))
                if st.get("baseline_seeded"):
                    held = sum(1 for c in st["coins"].values() if c.get("holding"))
                    print(f"    {p['label']}: seeded — {held}/{p['n']} coins bought")
                    done.add(p["label"])
            except Exception:
                pass
    if len(done) < len(plans):
        print("    (still seeding — check the bot logs; it may just be slow)")

    print(f"\n  Done. Bots reactivated at ${per:,.2f} each.")
    print("  NEXT (dev machine): ask Claude to regenerate the buy&hold benchmark so the")
    print("  dashboards reset to this new cycle.\n")


if __name__ == "__main__":
    main()
