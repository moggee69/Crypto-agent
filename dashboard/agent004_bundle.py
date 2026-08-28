"""Write Agent 004's trade + equity logs for its today-baseline (8 buys), so the
dashboard has records to plot. Kept minimal — it only started today."""
import json, csv, sys, os, time
from datetime import datetime, timezone
sys.path.insert(0, r"C:\Users\RyanMorgan\OneDrive - Curve Workplaces Ltd\Documents\Trading Bot\swing_bot")
import agent002_macd_rsi_backtest as bt

OUT = r"C:\Users\RYANMO~1\AppData\Local\Temp\claude\C--Users-RyanMorgan-OneDrive---Curve-Workplaces-Ltd-Documents-Trading-Bot\30f7b07b-7394-4615-908d-fbd835cc453c\scratchpad"
FEE = 0.006
iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
port = json.load(open(os.path.join(OUT, "a4_portfolio.json")))
coins = list(port["coins"])
prices = {}
for prod in coins:
    d = bt.daily(prod.replace("-USD", ""), 10)
    prices[prod] = d[-1]["c"]
    entry_t = int(d[-1]["t"])
now = int(time.time())
# trades: 8 baseline buys at the latest daily close
with open(os.path.join(OUT, "a4_trades.csv"), "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["timestamp_utc", "action", "product", "usd", "note"])
    for prod, c in port["coins"].items():
        w.writerow([iso(entry_t), "BUY", prod, round(c["cost_usd"], 2),
                    f"@ {c['buy_price']:.6g} baseline fee {c['cost_usd'] * FEE:.2f}"])
# equity: baseline point + now
base_eq = sum(c["qty"] * c["buy_price"] for c in port["coins"].values())
now_eq = sum(c["qty"] * prices[p] for p, c in port["coins"].items())
CAP = port["start_capital"]
with open(os.path.join(OUT, "a4_equity.csv"), "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["timestamp_utc", "equity", "pnl", "pnl_pct", "fees_paid"])
    w.writerow([iso(entry_t), round(base_eq, 2), round(base_eq - CAP, 2), round((base_eq / CAP - 1) * 100, 2), port["fees_paid"]])
    w.writerow([iso(now), round(now_eq, 2), round(now_eq - CAP, 2), round((now_eq / CAP - 1) * 100, 2), port["fees_paid"]])
print(f"Agent 004 bundle: baseline ${base_eq:.2f} -> now ${now_eq:.2f} ({(now_eq/CAP-1)*100:+.1f}%), 8 baseline buys")
