"""Paper portfolio for the swing bot — one independent cash bucket per coin.

Each coin gets an equal slice of the capital and trades on its own (buy its
dip, ride, sell on the trend break), mirroring how the strategy was backtested.
State persists to JSON; trades and equity snapshots go to CSVs.
"""
import csv
import json
import os
from datetime import datetime, timezone

import broker
import notify

TRADES = "swing_trades.csv"
EQUITY = "swing_equity.csv"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Portfolio:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.name = cfg.get("display_name", "Utility position bot 002")
        self.fee = cfg.get("fee_pct", 0.6)
        self.state_file = cfg.get("state_file", "swing_portfolio.json")
        self.state = self._load()
        self.live = broker.is_live(cfg)          # all three gates open? (else paper)
        self.broker = broker.Broker(cfg) if self.live else None
        # live startup (reconcile + one-time baseline seed) runs in start_live(),
        # called from main() after the banner so the log reads cleanly.

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
            "peak_equity": total,   # high-water mark, for the MaxDrawdown guard
            "fees_paid": 0.0,
            "coins": {p: {"cash": per, "holding": False, "qty": 0.0,
                          "buy_price": 0.0, "cost_usd": 0.0, "peak_price": 0.0,
                          "armed": False, "last_daily_ts": 0, "last_4h_ts": 0} for p in coins},
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
        if self.live:
            return self._live_buy(product, st, usd, note)
        fee = usd * self.fee / 100
        st["qty"] = (usd - fee) / price
        st["buy_price"] = price
        st["cost_usd"] = usd
        st["cash"] = 0.0
        st["holding"] = True
        self.state["fees_paid"] += fee
        self._log_trade("BUY", product, usd, f"@ {price:.6g} {note} fee {fee:.2f}")
        print(f"  BUY  {product:<10} ${usd:,.2f} @ {price:,.6g}  ({note})")
        notify.push(f"{self.name} BUY",
                    f"{product}  ${usd:,.2f} @ {price:.6g}\n{note}", tags="green_circle")
        self.save()

    def sell(self, product, st, price, note) -> float | None:
        if not st["holding"]:
            return None
        if self.live:
            return self._live_sell(product, st, note)
        gross = st["qty"] * price
        fee = gross * self.fee / 100
        pnl = (gross - fee) - st["cost_usd"]
        st["cash"] = gross - fee
        st["holding"] = False
        st["qty"] = 0.0
        self.state["fees_paid"] += fee
        self._log_trade("SELL", product, gross, f"@ {price:.6g} {note} pnl {pnl:+.2f} fee {fee:.2f}")
        print(f"  SELL {product:<10} ${gross:,.2f} @ {price:,.6g}  ({note}, P&L {pnl:+.2f})")
        notify.push(f"{self.name} SELL",
                    f"{product}  ${gross:,.2f} @ {price:.6g}\nP&L {pnl:+.2f}  ({note})", tags="red_circle")
        self.save()
        return pnl

    # ---------------- LIVE trading (real Coinbase orders) ----------------
    def _live_buy(self, product, st, usd, note):
        if broker.kill_switch_engaged():
            print(f"  [live] kill-switch set — skipping {product} BUY")
            return
        try:
            fill = self.broker.market_buy(product, usd)
        except broker.BrokerError as e:
            print(f"  [live] BUY {product} FAILED: {e}")
            notify.push(f"{self.name} BUY FAILED", f"{product}\n{e}", tags="warning")
            return
        st["qty"] = fill.qty
        st["buy_price"] = fill.price
        st["cost_usd"] = fill.quote_spent
        st["cash"] = max(0.0, usd - fill.quote_spent)   # leftover if size was capped
        st["holding"] = True
        self.state["fees_paid"] += fill.fee
        self._log_trade("BUY", product, fill.quote_spent,
                        f"LIVE @ {fill.price:.6g} {note} fee {fill.fee:.2f} id {fill.order_id[:8]}")
        print(f"  BUY* {product:<10} ${fill.quote_spent:,.2f} @ {fill.price:,.6g}  (LIVE {note})")
        notify.push(f"{self.name} LIVE BUY",
                    f"{product}  ${fill.quote_spent:,.2f} @ {fill.price:.6g}\n{note}", tags="green_circle")
        self.save()

    def _live_sell(self, product, st, note):
        if broker.kill_switch_engaged():
            print(f"  [live] kill-switch set — skipping {product} SELL")
            return None
        base = product.split("-")[0]
        actual = self.broker.available(base)
        qty = min(st["qty"], actual) if actual > 0 else st["qty"]   # never oversell
        try:
            fill = self.broker.market_sell(product, qty)
        except broker.BrokerError as e:
            print(f"  [live] SELL {product} FAILED: {e}")
            notify.push(f"{self.name} SELL FAILED", f"{product}\n{e}", tags="warning")
            return None
        proceeds = fill.quote_spent           # gross - fee, for a sell
        pnl = proceeds - st["cost_usd"]
        st["cash"] = proceeds
        st["holding"] = False
        st["qty"] = 0.0
        self.state["fees_paid"] += fill.fee
        self._log_trade("SELL", product, fill.qty * fill.price,
                        f"LIVE @ {fill.price:.6g} {note} pnl {pnl:+.2f} fee {fill.fee:.2f} id {fill.order_id[:8]}")
        print(f"  SELL*{product:<10} ${proceeds:,.2f} @ {fill.price:,.6g}  (LIVE {note}, P&L {pnl:+.2f})")
        notify.push(f"{self.name} LIVE SELL",
                    f"{product}  ${proceeds:,.2f} @ {fill.price:.6g}\nP&L {pnl:+.2f}  ({note})", tags="red_circle")
        self.save()
        return pnl

    def reconcile(self):
        """Sync each coin's HOLDING to the real exchange balance (live only).

        The exchange is the source of truth for coin quantities. This catches an
        unrecorded fill after a mid-order crash (adopt the position) and a
        position that vanished off-exchange (mark flat). Per-coin USD *cash*
        buckets are a bot-internal split the exchange can't tell us, so they're
        left to the per-trade fill accounting."""
        if not self.live:
            return
        for product, st in self.state["coins"].items():
            base = product.split("-")[0]
            actual = self.broker.available(base)
            m = self.broker.meta(product)
            dust = m["base_min"] or 1e-9
            held_on_exchange = actual > dust
            if held_on_exchange and not st["holding"]:
                px = self.broker.last_fill_price(product) or st.get("buy_price") or 0.0
                st["qty"] = actual
                st["holding"] = True
                if px:
                    st["buy_price"] = px
                    st["cost_usd"] = actual * px
                    st["peak_price"] = px
                msg = f"adopted {product} {actual:g} @ {px or '?'}"
                print(f"  [reconcile] {msg}")
                notify.push(f"{self.name} RECONCILE", msg, tags="warning")
            elif not held_on_exchange and st["holding"]:
                st["qty"] = 0.0
                st["holding"] = False
                print(f"  [reconcile] {product} not on exchange -> marked flat")
                notify.push(f"{self.name} RECONCILE", f"{product} not on exchange -> flat", tags="warning")
            elif held_on_exchange and abs(actual - st["qty"]) > dust:
                print(f"  [reconcile] {product} qty {st['qty']:g} -> {actual:g} (exchange truth)")
                st["qty"] = actual
        self.save()

    def start_live(self):
        """Live startup: reconcile to the real account, then seed the baseline once."""
        if not self.live:
            return
        self.reconcile()
        self.seed_baseline()

    def seed_baseline(self):
        """One-time: on the first live run, market-buy each coin's cash bucket to
        establish the starting position, then let the swing logic manage it from
        there. Skips coins already held; runs once (guarded by baseline_seeded)."""
        if not (self.live and self.cfg.get("live", {}).get("seed_baseline", False)):
            return
        if self.state.get("baseline_seeded"):
            return
        if broker.kill_switch_engaged():
            print("  [seed] kill-switch set — skipping baseline seed")
            return
        print("  [seed] establishing baseline positions (one-time market buys)...")
        for product, st in self.state["coins"].items():
            if st["holding"] or st["cash"] <= 0:
                continue
            self._live_buy(product, st, st["cash"], "baseline seed")
            if st["holding"]:                      # buy succeeded
                st["peak_price"] = st["buy_price"]
                st["armed"] = False
        self.state["baseline_seeded"] = True
        self.save()
        notify.push(f"{self.name} BASELINE", "baseline positions established (live)")

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
        self.state["peak_equity"] = max(self.state.get("peak_equity", start), eq)
        new = not os.path.exists(EQUITY)
        with open(EQUITY, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp_utc", "equity", "pnl", "pnl_pct", "fees_paid"])
            w.writerow([_now(), round(eq, 2), round(eq - start, 2),
                        round((eq - start) / start * 100, 2), round(self.state["fees_paid"], 2)])
        return eq
