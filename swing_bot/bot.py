"""Position Trader Agent 002 — MACD/RSI swing, polling-based paper trader.

Every `poll_seconds` it fetches each coin's recent daily candles and, on a
newly-closed candle, applies the strategy:

  BUY  when the MACD line crosses above its signal line (momentum turns up) while
       RSI is below `rsi_buy_max` — a dip that's turning, not chasing strength.
  SELL once RSI has reached `rsi_overbought` since entry (armed), when price then
       fades `trail_pct` from its peak. No downside stop — holds through dips.

Paper by default (dry_run). One cash bucket per coin. Runs unattended under
systemd; safe to restart — it only ever acts on the latest completed candle.

Usage:
    python bot.py
"""
import os
import signal
import sys
import time

import yaml

import data
import strategy
from portfolio import Portfolio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import risk_guard  # noqa: E402  (shared module, resolved via the path insert above)

DAILY = 86400


def process_coin(product, st, daily, cfg, pf, guard, liquidity_cfg,
                 now, equity_now, peak_equity):
    """MACD/RSI swing on daily candles. Acts only on a newly-closed daily candle.
    Entries pass the shared risk guards via `cleared_to_buy`; exits feed realised
    P&L back to the guard.

      BUY  : MACD bullish cross + RSI < rsi_buy_max (buy the dip-turn).
      SELL : once RSI has hit rsi_overbought since entry (armed), sell on a
             trail_pct fade from the peak. No downside stop — holds through dips."""
    if len(daily) < 40:
        return
    en, ex = cfg["entry"], cfg["exit"]
    closes = [c["c"] for c in daily]
    macd, sig = strategy.macd_lines(closes)
    rsis = strategy.rsi(closes)
    latest = daily[-1]
    if latest["t"] <= st["last_daily_ts"]:
        return
    cur_rsi = rsis[-1]

    def cleared_to_buy() -> bool:
        halt = guard.stoploss_halt(now)
        if halt:
            print(f"  [guard] entry halted: {halt}")
            return False
        blocked, reason, hu, pk = guard.drawdown_check(
            now, equity_now, peak_equity, pf.state.get("dd_halt_until", 0.0))
        pf.state["peak_equity"] = pk
        pf.state["dd_halt_until"] = hu
        if blocked:
            print(f"  [guard] entry halted: {reason}")
            return False
        ok, why = risk_guard.liquidity_ok(product, liquidity_cfg)
        if not ok:
            print(f"  [guard] skip {product}: {why}")
            return False
        return True

    if st["holding"]:
        st["peak_price"] = max(st.get("peak_price") or st["buy_price"], latest["h"])
        if cur_rsi is not None and cur_rsi >= ex["rsi_overbought"]:
            st["armed"] = True                      # run overheated — now watch for the fade
        if st.get("armed") and latest["c"] <= st["peak_price"] * (1 - ex["trail_pct"] / 100):
            pnl = pf.sell(product, st, latest["c"], f"RSI>{ex['rsi_overbought']} then -{ex['trail_pct']}% fade")
            if pnl is not None:
                guard.record_exit(now, pnl)
            st["armed"] = False
            st["peak_price"] = 0.0
    else:
        if (strategy.macd_bull_cross(macd, sig) and cur_rsi is not None
                and cur_rsi < en["rsi_buy_max"] and cleared_to_buy()):
            pf.buy(product, st, latest["c"], f"MACD turn, RSI {cur_rsi:.0f}")
            st["peak_price"] = latest["h"]
            st["armed"] = False
    st["last_daily_ts"] = latest["t"]


def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    pf = Portfolio(cfg)
    products = cfg["watchlist"]
    guard = risk_guard.RiskGuard(cfg.get("protections"))
    liquidity_cfg = cfg.get("liquidity")

    def shutdown(signum, frame):
        print("\n[bot] shutting down - saving state...")
        pf.save()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    mode = "LIVE TRADING — REAL MONEY" if pf.live else "DRY RUN (paper)"
    name = cfg.get("display_name", "Utility position bot 002")
    print(f"=== {name}  ·  MACD/RSI swing  |  {mode} ===")
    if pf.live:
        import broker
        print(f"!!! LIVE orders active. Max order ${pf.broker.max_order_usd:g}. "
              f"Kill-switch: create file '{broker.KILL_SWITCH}' to freeze. !!!", flush=True)
    elif not cfg.get("dry_run", True):
        print("(dry_run is off but live gate is CLOSED — still paper. "
              "Set live.live_trading + API keys to go live.)")
    print(f"Watching: {', '.join(products)}")
    print(f"Buy: MACD bullish cross + RSI < {cfg['entry']['rsi_buy_max']}  |  "
          f"Sell: RSI >= {cfg['exit']['rsi_overbought']} then -{cfg['exit']['trail_pct']}% fade "
          f"(no downside stop — holds through dips)")
    print(f"Polling every {cfg['poll_seconds']}s\n", flush=True)

    last_prices: dict[str, float] = {}
    while True:
        now = time.time()
        equity_now = pf.equity(last_prices)      # from the previous cycle's prices
        peak_equity = pf.state.get("peak_equity", pf.state["start_capital"])
        prices = {}
        for product in products:
            st = pf.coin_state(product)
            daily = data.fetch_candles(product, DAILY, 60)   # enough for MACD(26/9) + RSI(14)
            if daily:
                prices[product] = daily[-1]["c"]
            process_coin(product, st, daily, cfg, pf, guard, liquidity_cfg,
                         now, equity_now, peak_equity)
        pf.save()
        eq = pf.log_equity(prices)
        last_prices = prices
        held = [p for p, s in pf.state["coins"].items() if s["holding"]]
        print(f"[hb] equity ${eq:,.2f} | holding: {', '.join(held) or 'flat'}", flush=True)
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
