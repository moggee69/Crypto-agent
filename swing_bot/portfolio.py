"""Paper portfolio for the swing bot — one independent cash bucket per coin.

Each coin gets an equal slice of the capital and trades on its own (buy its
dip, ride, sell on the trend break), mirroring how the strategy was backtested.
State persists to JSON; trades and equity snapshots go to CSVs.
"""
import csv
import json
import os
from datetime import datetime, timezone

import notify

TRADES = "swing_trades.csv"
EQUITY = "swing_equity.csv"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Portfolio:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.fee = cfg.get("fee_pct", 0.6)
        self.state_file = cfg.get("state_file", "swing_portfolio.json")
        self.state = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                return json.load(f)
        total = self.cfg["capital"]["total_usd"]
        coins = self.cfg["watchlist"]
        per = total / len(coins)
        return {
            "start_capital": total,
            "per_coin": per,
            "fees_paid": 0.0,
            "coins": {p: {"cash": per, "holding": False, "qty": 0.0,
                          "buy_price": 0.0, "cost_usd": 0.0,
                          "last_daily_ts": 0, "last_4h_ts": 0} for p in coins},
        }

    def coin_state(self, product: str) -> dict:
        return self.state["coins"][product]

    def save(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def _log_trade(self, action, product, usd, note):
        new = not os.path.exists(TRADES)
        with open(TRADES, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp_utc", "action", "product", "usd", "note"])
            w.writerow([_now(), action, product, round(usd, 2), note])

    def buy(self, product, st, price, note):
        usd = st["cash"]
        if usd <= 0 or price <= 0:
            return
        fee = usd * self.fee / 100
        st["qty"] = (usd - fee) / price
        st["buy_price"] = price
        st["cost_usd"] = usd
        st["cash"] = 0.0
        st["holding"] = True
        self.state["fees_paid"] += fee
        self._log_trade("BUY", product, usd, f"@ {price:.6g} {note} fee {fee:.2f}")
        print(f"  BUY  {product:<10} ${usd:,.2f} @ {price:,.6g}  ({note})")
        notify.push("Swing bot BUY",
                    f"{product}  ${usd:,.2f} @ {price:.6g}\n{note}", tags="green_circle")
        self.save()

    def sell(self, product, st, price, note):
        if not st["holding"]:
            return
        gross = st["qty"] * price
        fee = gross * self.fee / 100
        pnl = (gross - fee) - st["cost_usd"]
        st["cash"] = gross - fee
        st["holding"] = False
        st["qty"] = 0.0
        self.state["fees_paid"] += fee
        self._log_trade("SELL", product, gross, f"@ {price:.6g} {note} pnl {pnl:+.2f} fee {fee:.2f}")
        print(f"  SELL {product:<10} ${gross:,.2f} @ {price:,.6g}  ({note}, P&L {pnl:+.2f})")
        notify.push("Swing bot SELL",
                    f"{product}  ${gross:,.2f} @ {price:.6g}\nP&L {pnl:+.2f}  ({note})", tags="red_circle")
        self.save()

    def equity(self, prices: dict) -> float:
        total = 0.0
        for p, st in self.state["coins"].items():
            total += st["cash"]
            if st["holding"]:
                total += st["qty"] * prices.get(p, st["buy_price"])
        return total

    def log_equity(self, prices: dict) -> float:
        eq = self.equity(prices)
        start = self.state["start_capital"]
        new = not os.path.exists(EQUITY)
        with open(EQUITY, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp_utc", "equity", "pnl", "pnl_pct", "fees_paid"])
            w.writerow([_now(), round(eq, 2), round(eq - start, 2),
                        round((eq - start) / start * 100, 2), round(self.state["fees_paid"], 2)])
        return eq
