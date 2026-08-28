"""Backfill each bot's MACD/RSI strategy from the mid-July baseline to today and
write it as the bot's REAL paper record: end-of-run portfolio state + full trade
log (swing_trades.csv) + daily equity log (swing_equity.csv). Deploying these
makes the July->now trades the bot's actual live history, continuing forward."""
import json, csv, sys, os
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\RyanMorgan\OneDrive - Curve Workplaces Ltd\Documents\Trading Bot\swing_bot")
import agent002_macd_rsi_backtest as bt
import strategy as st

OUT_DIR = r"C:\Users\RYANMO~1\AppData\Local\Temp\claude\C--Users-RyanMorgan-OneDrive---Curve-Workplaces-Ltd-Documents-Trading-Bot\30f7b07b-7394-4615-908d-fbd835cc453c\scratchpad"
ENTRY_DAY = int(datetime(2026, 7, 15, tzinfo=timezone.utc).timestamp()) // 86400 * 86400
CAP, FEE = 500.0, 0.006
RSI_BUY, RSI_OB, TRAIL = 40, 75, 0.10
iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()


def simulate(coins, botcode, tag):
    per = CAP / len(coins)
    dailies = {c: bt.daily(c, 120) for c in coins}
    coin_val = {c: {} for c in coins}
    trades = []           # (t, action, product, usd, note, fee)
    coins_state = {}
    for c in coins:
        d = dailies[c]
        closes = [x["c"] for x in d]
        macd, sig = st.macd_lines(closes)
        rs = st.rsi(closes)
        i0 = next((k for k, b in enumerate(d) if b["t"] >= ENTRY_DAY), 0)
        buy = d[i0]["c"]; cost = per; qty = (per * (1 - FEE)) / buy
        cash = 0.0; peak = d[i0]["h"]; armed = False; holding = True
        trades.append((int(d[i0]["t"]), "BUY", c + "-USD", round(per, 2),
                       f"@ {buy:.6g} baseline fee {per * FEE:.2f}", per * FEE))
        coin_val[c][int(d[i0]["t"])] = qty * d[i0]["c"]
        for i in range(i0 + 1, len(d)):
            b = d[i]
            if holding:
                if b["h"] > peak:
                    peak = b["h"]
                if rs[i] is not None and rs[i] >= RSI_OB:
                    armed = True
                if armed and b["c"] <= peak * (1 - TRAIL):
                    gross = qty * b["c"]; f = gross * FEE; pnl = (gross - f) - cost; cash = gross - f
                    trades.append((int(b["t"]), "SELL", c + "-USD", round(gross, 2),
                                   f"@ {b['c']:.6g} RSI>{RSI_OB} -{int(TRAIL*100)}% fade pnl {pnl:+.2f} fee {f:.2f}", f))
                    holding, qty, armed = False, 0.0, False
            elif (rs[i] is not None and rs[i] < RSI_BUY and macd[i - 1] <= sig[i - 1] and macd[i] > sig[i]):
                cost = cash; buy = b["c"]; qty = (cash * (1 - FEE)) / buy
                trades.append((int(b["t"]), "BUY", c + "-USD", round(cost, 2),
                               f"@ {buy:.6g} MACD turn (RSI {rs[i]:.0f}) fee {cost * FEE:.2f}", cost * FEE))
                cash = 0.0; peak = b["h"]; armed = False; holding = True
            coin_val[c][int(b["t"])] = cash + qty * b["c"]
        coins_state[c + "-USD"] = {"cash": round(cash, 6), "holding": holding, "qty": qty,
                                   "buy_price": buy if holding else 0.0, "cost_usd": cost if holding else 0.0,
                                   "peak_price": peak if holding else 0.0, "armed": armed,
                                   "last_daily_ts": int(d[-1]["t"]), "last_4h_ts": 0}
    # daily equity curve + cumulative fees
    days = sorted({t for c in coins for t in coin_val[c]})
    trades.sort()
    eq_rows, fees_total = [], 0.0
    fee_by_t = {}
    for t, *_rest, f in trades:
        fee_by_t[t] = fee_by_t.get(t, 0.0) + f
    for day in days:
        vals = [coin_val[c].get(day) for c in coins]
        if not all(v is not None for v in vals):
            continue
        cumfee = sum(f for t, f in fee_by_t.items() if t <= day)
        eqv = sum(vals)
        eq_rows.append([iso(day), round(eqv, 2), round(eqv - CAP, 2), round((eqv / CAP - 1) * 100, 2), round(cumfee, 2)])
    fees_total = round(sum(f for *_x, f in trades), 2)
    peak_eq = max((r[1] for r in eq_rows), default=CAP)
    portfolio = {"start_capital": CAP, "per_coin": per, "fees_paid": fees_total,
                 "peak_equity": round(peak_eq, 2), "dd_halt_until": 0.0, "coins": coins_state}
    # write deploy bundle
    json.dump(portfolio, open(os.path.join(OUT_DIR, f"{tag}_portfolio.json"), "w"), indent=2)
    with open(os.path.join(OUT_DIR, f"{tag}_trades.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["timestamp_utc", "action", "product", "usd", "note"])
        for t, a, prod, usd, note, _f in trades:
            w.writerow([iso(t), a, prod, usd, note])
    with open(os.path.join(OUT_DIR, f"{tag}_equity.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["timestamp_utc", "equity", "pnl", "pnl_pct", "fees_paid"])
        w.writerows(eq_rows)
    buys = sum(1 for t in trades if t[1] == "BUY"); sells = sum(1 for t in trades if t[1] == "SELL")
    held = sum(1 for s in coins_state.values() if s["holding"])
    print(f"{tag} ({botcode}): {eq_rows[-1][1]} ({eq_rows[-1][3]:+}%) | {buys} buys / {sells} sells | holding {held}/{len(coins)} | fees ${fees_total}")


simulate(["XLM", "HBAR", "XRP", "AVAX", "LINK", "ONDO", "FLR", "HYPE"], "SW", "sw")
simulate(["BTC", "SOL", "ETH", "ZEC", "ADA", "SUI", "LTC", "ICP"], "SL", "sl")
print("wrote deploy bundles: {sw,sl}_{portfolio.json,trades.csv,equity.csv}")
