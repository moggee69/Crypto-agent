"""Walk-forward backtest of the swing strategy — the honest "does it beat holding?" test.

Reuses the LIVE decision functions from strategy.py (is_green / red_run_ending /
sma / insurance_ok), so we validate the real code, not a lookalike. Discipline:

  * No look-ahead: on day i the strategy sees only candles[:i+1]; a day's own
    close is the newest bar it may act on (exactly like the live bot, which only
    ever acts on a *completed* candle).
  * Walk-forward: performance is reported per rolling out-of-sample window, not
    just over one lucky full-period run. A fixed-rule strategy that only wins in
    one window isn't robust.
  * Parameter robustness: a small grid shows whether the chosen params are a
    stable plateau or a fragile peak.
  * Benchmark: every number is compared against simply buying and holding the
    same coin over the same span — the bar the bot must clear to justify itself.

Scope: models the DAILY entry/exit path (the strategy's core). The intraday 4h
fallback is intentionally excluded — it needs 300d of hourly data per coin and
is a secondary path; this keeps the test clean and the thesis honest.

Usage (from swing_bot/):  python backtest_walkforward.py
"""
import statistics
import time
from datetime import datetime, timezone, timedelta

import requests
import yaml

import strategy

URL = "https://api.exchange.coinbase.com/products/{}/candles"


def fetch_daily(product: str, days: int = 600) -> list[dict]:
    """Up to `days` most-recent COMPLETED daily candles, oldest first. Coinbase
    caps a candles response at 300 rows, so page backwards in <300d chunks."""
    now = time.time()
    bars: dict[int, dict] = {}
    cursor_end = datetime.now(timezone.utc)
    remaining = days
    while remaining > 0:
        chunk = min(295, remaining)
        cursor_start = cursor_end - timedelta(days=chunk)
        for _ in range(3):
            try:
                r = requests.get(URL.format(product),
                                 params={"granularity": 86400,
                                         "start": cursor_start.isoformat(),
                                         "end": cursor_end.isoformat()},
                                 timeout=25)
                if r.status_code == 200:
                    for t, lo, hi, op, cl, vol in r.json():
                        if t + 86400 <= now:
                            bars[t] = {"t": t, "o": op, "h": hi, "l": lo, "c": cl}
                    break
            except Exception:
                pass
            time.sleep(1.0)
        cursor_end = cursor_start
        remaining -= chunk
        time.sleep(0.25)
    return [bars[t] for t in sorted(bars)]


def run_strategy(candles, p, fee, start_i, end_i):
    """Replay the daily strategy over [start_i, end_i). MAs use all history up to
    each day (so a mid-series window starts with warm averages, like the live bot).
    Returns (equity_multiple, n_trades) with 1.0 = untouched starting capital."""
    ins = {"enabled": p["insurance"], "uptrend_ma_days": p["uptrend_ma_days"]}
    cash, holding, qty, trades = 1.0, False, 0.0, 0
    for i in range(start_i, end_i):
        hist = candles[:i + 1]
        latest = hist[-1]
        ma_exit = strategy.sma(hist, p["ma_exit_days"])
        ma_long = strategy.sma(hist, p["uptrend_ma_days"]) if p["insurance"] else None
        if holding:
            if ma_exit is not None and latest["c"] < ma_exit:
                cash = qty * latest["c"] * (1 - fee)
                holding, qty, trades = False, 0.0, trades + 1
        elif strategy.is_green(latest):
            reds = strategy.red_run_ending(hist, len(hist) - 2)
            if p["min_red"] <= reds <= p["max_red"] and strategy.insurance_ok(ins, ma_long, latest["c"]):
                qty = cash * (1 - fee) / latest["c"]
                cash, holding, trades = 0.0, True, trades + 1
    final = cash if not holding else qty * candles[end_i - 1]["c"]
    return final, trades


def hold_multiple(candles, a, b):
    return candles[b - 1]["c"] / candles[a]["c"]


def pct(m):
    return f"{(m - 1) * 100:+6.1f}%"


def main():
    cfg = yaml.safe_load(open("config.yaml"))
    coins = cfg["watchlist"]
    fee = cfg.get("fee_pct", 0.6) / 100
    e, ex = cfg["entry"], cfg["exit"]
    base = {
        "min_red": e["min_red_candles"], "max_red": e["max_red_candles"],
        "ma_exit_days": ex["ma_exit_days"],
        "insurance": e["insurance_uptrend_filter"].get("enabled", True),
        "uptrend_ma_days": e["insurance_uptrend_filter"].get("uptrend_ma_days", 30),
    }
    warm = max(base["ma_exit_days"], base["uptrend_ma_days"] if base["insurance"] else 0)

    print("Fetching ~300d daily candles from Coinbase...\n")
    data = {}
    for c in coins:
        d = fetch_daily(c)
        if len(d) > warm + 20:
            data[c] = d
        else:
            print(f"  {c}: only {len(d)} candles - skipped")
        time.sleep(0.2)
    if not data:
        print("No usable data."); return
    # Align every coin to the common set of dates (coins have different-length
    # histories; without this, position i means a different calendar day per coin).
    common = sorted(set.intersection(*[{x["t"] for x in d} for d in data.values()]))
    data = {c: [x for x in d if x["t"] in common] for c, d in data.items()}
    span = len(common)
    d0 = datetime.fromtimestamp(common[0], timezone.utc).date()
    d1 = datetime.fromtimestamp(common[-1], timezone.utc).date()
    print(f"Usable coins: {len(data)} | common window {d0} .. {d1} ({span} days) | "
          f"fee {fee*100:.1f}%/side | warmup {warm}d\n")

    # ---------- 1) full-period, per coin ----------
    print("=" * 62)
    print("FULL PERIOD — strategy vs. buy & hold (per coin)")
    print("=" * 62)
    print(f"{'coin':<10}{'strategy':>11}{'buy&hold':>11}{'edge':>10}{'trades':>8}")
    s_mults, h_mults, wins = [], [], 0
    for c, d in data.items():
        sm, tr = run_strategy(d, base, fee, warm, len(d))
        hm = hold_multiple(d, warm, len(d))
        s_mults.append(sm); h_mults.append(hm)
        beat = sm > hm; wins += beat
        print(f"{c:<10}{pct(sm):>11}{pct(hm):>11}{pct(sm/hm):>10}{tr:>8}"
              f"{'  <-- beat hold' if beat else ''}")
    ps, ph = statistics.mean(s_mults), statistics.mean(h_mults)
    print("-" * 62)
    print(f"{'PORTFOLIO':<10}{pct(ps):>11}{pct(ph):>11}{pct(ps/ph):>10}"
          f"    (beat hold on {wins}/{len(data)} coins)")

    # ---------- 2) walk-forward windows (equal-weight portfolio) ----------
    n_win = 4
    edges = [warm + round(i * (span - warm) / n_win) for i in range(n_win + 1)]
    print("\n" + "=" * 62)
    print(f"WALK-FORWARD — {n_win} consecutive out-of-sample windows (portfolio)")
    print("=" * 62)
    print(f"{'window':<22}{'strategy':>11}{'buy&hold':>11}{'edge':>10}{'win?':>7}")
    wf_wins = 0
    for w in range(n_win):
        a, b = edges[w], edges[w + 1]
        sm = statistics.mean(run_strategy(d, base, fee, a, b)[0] for d in data.values())
        hm = statistics.mean(hold_multiple(d, a, b) for d in data.values())
        beat = sm > hm; wf_wins += beat
        d0 = datetime.fromtimestamp(next(iter(data.values()))[a]["t"], timezone.utc).date()
        d1 = datetime.fromtimestamp(next(iter(data.values()))[b - 1]["t"], timezone.utc).date()
        print(f"{str(d0)+'..'+str(d1):<22}{pct(sm):>11}{pct(hm):>11}{pct(sm/hm):>10}"
              f"{'  yes' if beat else '   no':>7}")
    print("-" * 62)
    print(f"Strategy beat buy & hold in {wf_wins}/{n_win} out-of-sample windows.")

    # ---------- 3) parameter robustness (full period, portfolio edge) ----------
    print("\n" + "=" * 62)
    print("PARAMETER ROBUSTNESS — portfolio edge vs hold across settings")
    print("=" * 62)
    print(f"{'ma_exit_days':>13}{'max_red':>9}{'strategy':>11}{'buy&hold':>11}{'edge':>10}")
    for mae in (5, 7, 10, 14):
        for mr in (3, 4, 5):
            p = dict(base, ma_exit_days=mae, max_red=mr)
            sm = statistics.mean(run_strategy(d, p, fee, warm, len(d))[0] for d in data.values())
            hm = statistics.mean(hold_multiple(d, warm, len(d)) for d in data.values())
            flag = "  <-- current" if (mae == base["ma_exit_days"] and mr == base["max_red"]) else ""
            print(f"{mae:>13}{mr:>9}{pct(sm):>11}{pct(hm):>11}{pct(sm/hm):>10}{flag}")

    print("\nReading it: 'edge' > 0% means the strategy beat simply holding over that\n"
          "span. Consistency across windows and across the grid matters far more\n"
          "than any single winning number.")


if __name__ == "__main__":
    main()
