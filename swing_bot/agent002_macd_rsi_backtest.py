"""Backtest the proposed Agent 002 MACD/RSI swing strategy on the fixed 8 coins.

Rules (daily candles):
  BUY  = MACD line crosses ABOVE its signal line (momentum turns up) while RSI is
         below `rsi_buy_max` (buying a dip-turn, not chasing).
  HOLD = ride it; no stop-loss on the downside (per the plan — hold through dips).
  SELL = once RSI has been ABOVE `rsi_ob` (overbought) since entry, sell when price
         fades `trail`% from its peak.
  Re-buy on the next qualifying MACD turn. One cash bucket per coin, equal weight.

Benchmarks each variant against simply holding the coin from day one — the number
the strategy has to justify itself against.

Run from swing_bot/:  python agent002_macd_rsi_backtest.py
"""
import time
from datetime import datetime, timezone, timedelta

import requests

WATCH = ["XLM", "HBAR", "XRP", "AVAX", "LINK", "ONDO", "FLR", "HYPE"]
CB = "https://api.exchange.coinbase.com/products/{}-USD/candles"
FEE = 0.006


def daily(sym, days=300):
    end = datetime.now(timezone.utc)
    bars = {}
    cur_end = end
    while (end - cur_end).days < days:
        start = cur_end - timedelta(days=295)
        for _ in range(3):
            try:
                r = requests.get(CB.format(sym), params={"granularity": 86400,
                                 "start": start.isoformat(), "end": cur_end.isoformat()}, timeout=20)
                if r.status_code == 200:
                    for t, lo, hi, op, cl, v in r.json():
                        bars[int(t)] = {"t": int(t), "o": op, "h": hi, "l": lo, "c": cl}
                    break
            except Exception:
                time.sleep(0.5)
        cur_end = start
    return [bars[t] for t in sorted(bars)]


def ema(vals, n):
    k = 2 / (n + 1); out = []; e = None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    g = sum(max(closes[i] - closes[i - 1], 0) for i in range(1, n + 1)) / n
    l = sum(max(closes[i - 1] - closes[i], 0) for i in range(1, n + 1)) / n
    out[n] = 100 - 100 / (1 + (g / l if l else 999))
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + (g / l if l else 999))
    return out


def indicators(bars):
    cl = [b["c"] for b in bars]
    macd = [a - b for a, b in zip(ema(cl, 12), ema(cl, 26))]
    sig = ema(macd, 9)
    return macd, sig, rsi(cl, 14)


def simulate(bars, rsi_buy_max, rsi_ob, trail, warm=35):
    macd, sig, rs = indicators(bars)
    cash, qty, entry, peak, armed = 1.0, 0.0, 0.0, 0.0, False
    holding = False
    trades = []
    for i in range(warm, len(bars)):
        b = bars[i]
        if rs[i] is None:
            continue
        if holding:
            peak = max(peak, b["h"])
            if rs[i] > rsi_ob:
                armed = True
            if armed and b["c"] <= peak * (1 - trail / 100):
                cash = qty * b["c"] * (1 - FEE)
                trades.append(cash / (entry_cash) - 1)   # per-trade return
                holding, qty, armed = False, 0.0, False
        else:
            cross = macd[i - 1] <= sig[i - 1] and macd[i] > sig[i]
            if cross and rs[i] < rsi_buy_max:
                entry_cash = cash
                qty = cash * (1 - FEE) / b["c"]; entry = b["c"]; peak = b["h"]
                cash, holding, armed = 0.0, True, False
    final = cash if not holding else qty * bars[-1]["c"]
    return final, len(trades), sum(1 for t in trades if t > 0)


def main():
    print("Fetching daily candles for the 8 coins...")
    data = {s: daily(s) for s in WATCH}
    span = min(len(d) for d in data.values())
    d0 = datetime.fromtimestamp(min(d[0]["t"] for d in data.values()), timezone.utc).date()
    d1 = datetime.fromtimestamp(max(d[-1]["t"] for d in data.values()), timezone.utc).date()
    print(f"  ~{span} daily candles/coin | {d0}..{d1}\n")

    variants = [("MACD cross, RSI<100 (no filter)", 100), ("MACD cross + RSI<50", 50), ("MACD cross + RSI<40", 40)]
    for label, rbm in variants:
        print("=" * 60)
        print(f"{label}   (sell: RSI>70 then -10% fade)")
        print("=" * 60)
        print(f"{'coin':<8}{'strategy':>11}{'buy&hold':>11}{'trades':>8}{'wins':>7}")
        s_mult, h_mult = [], []
        for s in WATCH:
            bars = data[s]
            fin, ntr, nw = simulate(bars, rbm, 70, 10)
            hold = bars[-1]["c"] / bars[35]["c"]
            s_mult.append(fin); h_mult.append(hold)
            print(f"{s:<8}{(fin-1)*100:>+10.1f}%{(hold-1)*100:>+10.1f}%{ntr:>8}{nw:>7}")
        ps = sum(s_mult) / len(s_mult); ph = sum(h_mult) / len(h_mult)
        beat = sum(1 for a, b in zip(s_mult, h_mult) if a > b)
        print("-" * 60)
        print(f"{'PORT':<8}{(ps-1)*100:>+10.1f}%{(ph-1)*100:>+10.1f}%   beat hold on {beat}/8 coins\n")


if __name__ == "__main__":
    main()
