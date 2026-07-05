"""Swing Bot — dip-buy + trend-break-exit, polling-based paper trader.

Every `poll_seconds` it fetches each coin's latest completed daily and 4h
candles and, on a newly-closed candle, applies the strategy:

  BUY  when 2-4 red daily candles are followed by a green one (the turn) — or,
       if the fall runs past `extended_fall_days`, the first green 4h candle.
       (Optional insurance: only when price is above its long MA = uptrend.)
  SELL when the daily close drops below its short MA (`ma_exit_days`).

Paper by default (dry_run). One cash bucket per coin. Runs unattended under
systemd; safe to restart — it only ever acts on the latest completed candle.

Usage:
    python bot.py
"""
import signal
import sys
import time

import yaml

import data
import strategy
from portfolio import Portfolio

DAILY = 86400


def process_coin(product, st, daily, h4, cfg, pf):
    """Apply the strategy to one coin given its recent candles."""
    if not daily:
        return
    exit_n = cfg["exit"]["ma_exit_days"]
    ecfg = cfg["entry"]
    ins = ecfg["insurance_uptrend_filter"]
    ma_exit = strategy.sma(daily, exit_n)
    ma_long = strategy.sma(daily, ins["uptrend_ma_days"]) if ins.get("enabled") else None
    latest = daily[-1]

    # ---- daily candle close: exit check, then entry check ----
    if latest["t"] > st["last_daily_ts"]:
        if st["holding"]:
            if ma_exit is not None and latest["c"] < ma_exit:
                pf.sell(product, st, latest["c"], f"ma{exit_n}-break")
        else:
            if strategy.is_green(latest):
                reds = strategy.red_run_ending(daily, len(daily) - 2)  # reds before the green
                if ecfg["min_red_candles"] <= reds <= ecfg["max_red_candles"] \
                        and strategy.insurance_ok(ins, ma_long, latest["c"]):
                    pf.buy(product, st, latest["c"], f"daily-turn ({reds} red)")
        st["last_daily_ts"] = latest["t"]

    # ---- 4h fallback: only while flat and the fall has run past the limit ----
    if not st["holding"] and h4:
        cur_reds = strategy.red_run_ending(daily, len(daily) - 1)  # ongoing red run
        if cur_reds > ecfg["extended_fall_days"]:
            l4 = h4[-1]
            if l4["t"] > st["last_4h_ts"]:
                if strategy.is_green(l4) and strategy.insurance_ok(ins, ma_long, l4["c"]):
                    pf.buy(product, st, l4["c"], f"4h-fallback ({cur_reds} red days)")
                st["last_4h_ts"] = l4["t"]


def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    pf = Portfolio(cfg)
    products = cfg["watchlist"]

    def shutdown(signum, frame):
        print("\n[bot] shutting down - saving state...")
        pf.save()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    mode = "DRY RUN (paper)" if cfg.get("dry_run", True) else "LIVE"
    ins = cfg["entry"]["insurance_uptrend_filter"]
    print(f"=== Swing Bot | {mode} ===")
    print(f"Watching: {', '.join(products)}")
    print(f"Buy: {cfg['entry']['min_red_candles']}-{cfg['entry']['max_red_candles']} red "
          f"then green turn (4h fallback past {cfg['entry']['extended_fall_days']}d)  |  "
          f"Sell: close < {cfg['exit']['ma_exit_days']}d MA")
    print(f"Insurance uptrend filter: {'ON (>%dd MA)' % ins['uptrend_ma_days'] if ins.get('enabled') else 'OFF'}")
    print(f"Polling every {cfg['poll_seconds']}s\n", flush=True)

    while True:
        prices = {}
        for product in products:
            st = pf.coin_state(product)
            daily = data.fetch_candles(product, DAILY, 40)
            h4 = data.fetch_4h(product, 12)
            if h4:
                prices[product] = h4[-1]["c"]
            elif daily:
                prices[product] = daily[-1]["c"]
            process_coin(product, st, daily, h4, cfg, pf)
        pf.save()
        eq = pf.log_equity(prices)
        held = [p for p, s in pf.state["coins"].items() if s["holding"]]
        print(f"[hb] equity ${eq:,.2f} | holding: {', '.join(held) or 'flat'}", flush=True)
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
