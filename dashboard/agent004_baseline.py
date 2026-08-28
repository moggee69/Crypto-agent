"""Today-baseline for Agent 004: hold the top-8 bought at the latest daily close
(no July backfill — honest forward start). Writes swing_portfolio.json."""
import json, sys, os
from datetime import datetime, timezone
sys.path.insert(0, r"C:\Users\RyanMorgan\OneDrive - Curve Workplaces Ltd\Documents\Trading Bot\swing_bot")
import agent002_macd_rsi_backtest as bt
import strategy

OUT = r"C:\Users\RYANMO~1\AppData\Local\Temp\claude\C--Users-RyanMorgan-OneDrive---Curve-Workplaces-Ltd-Documents-Trading-Bot\30f7b07b-7394-4615-908d-fbd835cc453c\scratchpad"
COINS = ["ZEC", "SOL", "LINK", "ETH", "XRP", "ADA", "HYPE", "BTC"]
CAP, FEE = 500.0, 0.006
per = CAP / len(COINS)
state = {"start_capital": CAP, "per_coin": per, "fees_paid": round(per * FEE * len(COINS), 2),
         "peak_equity": CAP, "dd_halt_until": 0.0, "coins": {}}
for c in COINS:
    d = bt.daily(c, 120)
    last = d[-1]
    buy = last["c"]; qty = (per * (1 - FEE)) / buy
    rs = strategy.rsi([x["c"] for x in d])
    armed = rs[-1] is not None and rs[-1] >= 75      # already overheated at entry?
    state["coins"][c + "-USD"] = {"cash": 0.0, "holding": True, "qty": qty, "buy_price": buy,
                                  "cost_usd": per, "peak_price": last["h"], "armed": armed,
                                  "last_daily_ts": int(d[-2]["t"]), "last_4h_ts": 0}
json.dump(state, open(os.path.join(OUT, "a4_portfolio.json"), "w"), indent=2)
ed = datetime.fromtimestamp(d[-1]["t"], timezone.utc).date()
print(f"Agent 004 today-baseline ({ed}): bought {len(COINS)} at ${per:.2f} each = ${CAP}")
print("armed at entry:", [c for c in COINS if state["coins"][c + "-USD"]["armed"]])
