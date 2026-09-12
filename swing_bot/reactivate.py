"""RESET & REACTIVATE — the mirror of sell_all.py. Re-arms the two live bots (002 +
003) after a bull-top cash-out: resets each to a fresh all-cash baseline at a chosen
capital, then restarts them so they seed-buy every coin at CURRENT (bottom) prices
and resume normal management.

    *** THE RESTART PLACES REAL BUY ORDERS. Claude does NOT run the live path. ***

Does NOT touch git-tracked config.yaml or bench_basis.json. Capital is written
straight into the fresh portfolio JSON. After seeding, regenerate the buy&hold
benchmark from a dev machine (ask Claude: "refresh the benchmark").

FOUR gates for a real reset — ALL required:
  1. --execute flag       (omit = DRY RUN: prints the plan, changes NOTHING)
  2. passcode             (must match /opt/crypto-agent/sell_passcode.txt)
  3. typed confirmation   (CLI: type REACTIVATE  |  app: the on-screen confirm step)
  4. enough free USDC      (>= total to deploy, unless --force)

Modes:
    python reactivate.py                        # DRY RUN, human plan (per = half your USDC)
    python reactivate.py --per 400 --json       # DRY RUN, JSON (app preview)
    python reactivate.py --per 400 --execute    # REAL, interactive
    python reactivate.py --per 400 --execute --web --json   # REAL, non-interactive (app);
                                                #   passcode via REACTIVATE_PASSCODE env
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
    bucket = per / len(coins)
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


def build_plan(per_arg):
    usdc = usdc_balance()
    per = per_arg if per_arg is not None else float(math.floor(usdc / 2))
    plans, warnings = [], []
    for label, botdir, svc in BOTS:
        cfg = bot_cfg(botdir)
        coins = cfg["watchlist"]
        seed = cfg.get("live", {}).get("seed_baseline", False)
        live_on = (not cfg.get("dry_run", True)) and cfg.get("live", {}).get("live_trading", False)
        if not seed:
            warnings.append(f"{label}: seed_baseline OFF (won't re-buy)")
        if not live_on:
            warnings.append(f"{label}: not live in config")
        plans.append({"label": label, "dir": botdir, "svc": svc, "coins": coins, "n": len(coins),
                      "state": os.path.join(botdir, cfg.get("state_file", "swing_portfolio.json"))})
    total = per * len(plans)
    return usdc, per, total, plans, warnings, usdc >= total


def do_reset_restart(per, plans, poll):
    ts = int(time.time())
    for p in plans:
        for fn in ("swing_portfolio.json", "swing_equity.csv", "swing_trades.csv"):
            fp = os.path.join(p["dir"], fn)
            if os.path.exists(fp):
                os.rename(fp, f"{fp}.pre_reactivate.{ts}")
        json.dump(fresh_state(per, p["coins"]), open(p["state"], "w"), indent=2)
        halt = os.path.join(p["dir"], "HALT")
        if os.path.exists(halt):
            os.remove(halt)
    subprocess.run(["systemctl", "restart"] + [p["svc"] for p in plans], check=False)
    deadline, done = time.time() + poll, {}
    while time.time() < deadline and len(done) < len(plans):
        time.sleep(5)
        for p in plans:
            if p["label"] in done:
                continue
            try:
                st = json.load(open(p["state"]))
                if st.get("baseline_seeded"):
                    done[p["label"]] = sum(1 for c in st["coins"].values() if c.get("holding"))
            except Exception:
                pass
    return done


def main():
    args = sys.argv[1:]
    execute = "--execute" in args
    force = "--force" in args
    web = "--web" in args
    as_json = "--json" in args
    per_arg = float(args[args.index("--per") + 1]) if "--per" in args else None

    usdc, per, total, plans, warnings, enough = build_plan(per_arg)

    if not execute:
        if as_json:
            print(json.dumps({"mode": "preview", "usdc": round(usdc, 2), "per": round(per, 2),
                              "total": round(total, 2), "dry_powder": round(usdc - total, 2),
                              "enough": enough, "warnings": warnings,
                              "bots": [{"label": p["label"], "n": p["n"],
                                        "per_coin": round(per / p["n"], 2)} for p in plans]}))
            return
        print("\n=== RESET & REACTIVATE PLAN ===\n")
        print(f"  free USDC now:        ${usdc:,.2f}")
        print(f"  capital per bot:      ${per:,.2f}   (deploy ${total:,.2f})")
        print(f"  USDC left dry:        ${usdc - total:,.2f}\n")
        for p in plans:
            print(f"  {p['label']:<14} ${per:,.2f} / {p['n']} coins (${per / p['n']:,.2f}/coin), restart {p['svc']}")
        for w in warnings:
            print(f"  !! {w}")
        if not enough:
            print(f"  !! NOT ENOUGH USDC: need ${total:,.2f}, have ${usdc:,.2f}")
        print("\n  On restart each bot reconciles flat then market-buys every bucket (REAL orders).")
        print("\n*** DRY RUN — nothing changed. Add --execute to reset + restart for real. ***\n")
        return

    def fail(msg):
        print(json.dumps({"ok": False, "error": msg}) if as_json else f"ABORT: {msg}")

    try:
        expected = open(PASSCODE_FILE).read().strip()
    except OSError:
        expected = ""
    if not expected:
        return fail("passcode not set")
    if web:
        if os.environ.get("REACTIVATE_PASSCODE", "") != expected:
            return fail("bad passcode")
    else:
        if getpass.getpass("  Passcode: ").strip() != expected:
            return fail("wrong passcode")
        if input('  Type "REACTIVATE" to confirm: ').strip() != "REACTIVATE":
            return fail("not confirmed")
    if not enough and not force:
        return fail(f"not enough USDC: need ${total:.2f}, have ${usdc:.2f}")
    if warnings and not force:
        return fail("; ".join(warnings))

    if not as_json:
        print("\n  Resetting + restarting...")
    done = do_reset_restart(per, plans, poll=90 if web else 180)
    out = {"ok": True, "mode": "executed", "per": round(per, 2),
           "seeded": len(done) == len(plans),
           "bots": [{"label": p["label"], "n": p["n"], "held": done.get(p["label"])} for p in plans]}
    if as_json:
        print(json.dumps(out))
    else:
        print(f"\n  Done. Reactivated at ${per:,.2f} each (seeded {len(done)}/{len(plans)}).")
        print("  NEXT (dev machine): ask Claude to regenerate the buy&hold benchmark.\n")


if __name__ == "__main__":
    main()
