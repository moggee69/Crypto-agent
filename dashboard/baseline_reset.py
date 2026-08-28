"""Build the mid-July baseline: reset Agent 002 to hold all 8 (bought 2026-07-15),
with peak/armed correctly seeded, and produce the historical equity curve + positions
for the dashboard."""
import json, sys, os
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\RyanMorgan\OneDrive - Curve Workplaces Ltd\Documents\Trading Bot\swing_bot")
import agent002_macd_rsi_backtest as bt
import strategy

OUT = r"C:\Users\RYANMO~1\AppData\Local\Temp\claude\C--Users-RyanMorgan-OneDrive---Curve-Workplaces-Ltd-Documents-Trading-Bot\30f7b07b-7394-4615-908d-fbd835cc453c\scratchpad"
WATCH = [w + "-USD" for w in bt.WATCH]
ENTRY = datetime(2026, 7, 15, tzinfo=timezone.utc).timestamp()
CAP, FEE = 500.0, 0.006
per = CAP / len(WATCH)

data = {w: bt.daily(w.replace("-USD", ""), 120) for w in WATCH}
state = {"start_capital": CAP, "per_coin": per, "fees_paid": 0.0,
         "peak_equity": 0.0, "dd_halt_until": 0.0, "coins": {}}
positions, ledger = [], []
# per-coin holdings
qtys = {}
for w in WATCH:
    d = data[w]
    i = next((k for k, b in enumerate(d) if b["t"] >= ENTRY), 0)
    buy = d[i]["c"]
    fee = per * FEE
    qty = (per - fee) / buy
    qtys[w] = (qty, i)
    peak = max(b["h"] for b in d[i:])
    rs = strategy.rsi([b["c"] for b in d])
    armed = any(rs[k] is not None and rs[k] >= 75 for k in range(i, len(d)))
    state["coins"][w] = {"cash": 0.0, "holding": True, "qty": qty, "buy_price": buy,
                         "cost_usd": per, "peak_price": peak, "armed": armed,
                         "last_daily_ts": int(d[-2]["t"]), "last_4h_ts": 0}
    state["fees_paid"] += fee
    now = d[-1]["c"]
    positions.append({"bot": "Swing", "coin": w.replace("-USD", ""), "buy": round(buy, 6),
                      "cur": round(now, 6), "pnl": round((now / buy - 1) * 100, 1)})
    ledger.append({"t": int(d[i]["t"]), "bot": "SW", "a": "BUY", "c": w.replace("-USD", ""),
                   "px": round(buy, 6), "fee": round(fee, 2), "pnl": None})
    ed = datetime.fromtimestamp(d[i]["t"], timezone.utc).date()
# equity curve entry -> now (union of dates)
alldays = sorted({b["t"] for w in WATCH for b in data[w] if b["t"] >= data[WATCH[0]][qtys[WATCH[0]][1]]["t"]})
closes = {w: {b["t"]: b["c"] for b in data[w]} for w in WATCH}
eq = []
for t in alldays:
    v = sum(qtys[w][0] * closes[w][t] for w in WATCH if t in closes[w])
    if v:
        eq.append([int(t), round(v, 2)])
cur = sum(qtys[w][0] * data[w][-1]["c"] for w in WATCH)
state["peak_equity"] = round(max(cur, CAP), 2)

json.dump(state, open(os.path.join(OUT, "swing_portfolio.json"), "w"), indent=2)
json.dump({"eq": eq, "cur": round(cur, 2), "pnl": round((cur / CAP - 1) * 100, 2),
           "pos": positions, "ledger": ledger},
          open(os.path.join(OUT, "baseline_sw.json"), "w"))
print(f"Baseline entry {ed} | current basket ${cur:,.2f} ({(cur/CAP-1)*100:+.1f}%) | eq pts {len(eq)}")
print("armed coins:", [w for w in WATCH if state["coins"][w]["armed"]])
print("wrote swing_portfolio.json + baseline_sw.json")
