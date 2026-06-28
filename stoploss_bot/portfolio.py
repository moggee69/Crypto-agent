"""Order execution + position tracking for the trailing stop-loss bot.

Dry-run keeps a *simulated* portfolio (cash + positions) persisted to JSON, just
like the daily momentum bot in the parent folder, so paper trading builds a real
track record with simulated taker fees. Each position remembers its entry price
and running peak so the trailing stop can be evaluated on every tick. Live mode
routes orders through the Coinbase Advanced Trade REST API; the local position
model stays authoritative for the trailing logic in both modes.

Every intended trade is appended to trades_log.csv, and an equity snapshot is
appended to equity_history.csv — the same column layout the parent bot's
report.py already understands, so it can chart this bot's curve too.
"""
import csv
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

LOG_FILE = "trades_log.csv"
EQUITY_FILE = "equity_history.csv"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_trade(action: str, product: str, usd: float, dry_run: bool, note: str = ""):
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp_utc", "mode", "action", "product", "usd_amount", "note"])
        w.writerow([_now(), "DRY_RUN" if dry_run else "LIVE", action, product, usd, note])


class Portfolio:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dry_run = cfg["dry_run"]
        paper_cfg = cfg.get("paper", {})
        self.fee_pct = paper_cfg.get("fee_pct", 0.0)
        self.state_file = paper_cfg.get("state_file", "paper_portfolio.json")
        self.client = None
        if not self.dry_run:
            from coinbase.rest import RESTClient  # pip install coinbase-advanced-py
            self.client = RESTClient(
                api_key=os.environ["COINBASE_API_KEY"],
                api_secret=os.environ["COINBASE_API_SECRET"],
            )
        self.state = self._load_state()

    # ---------- persisted state ----------
    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                return json.load(f)
        cap = self.cfg["risk"]["total_capital_usd"]
        return {
            "start_capital": cap,
            "cash_usd": cap,
            "fees_paid": 0.0,
            # product -> {qty, entry_price, peak_price, last_price, opened_at, cost_usd}
            "positions": {},
            # product -> iso timestamp until which re-entry is blocked
            "cooldown_until": {},
        }

    def save(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    # ---------- queries ----------
    def has_position(self, product: str) -> bool:
        return product in self.state["positions"]

    def num_positions(self) -> int:
        return len(self.state["positions"])

    def in_cooldown(self, product: str) -> bool:
        until = self.state["cooldown_until"].get(product)
        return bool(until) and datetime.now(timezone.utc) < datetime.fromisoformat(until)

    def equity(self, prices: dict[str, float]) -> float:
        cash = self.state["cash_usd"]
        crypto = sum(p["qty"] * prices.get(prod, p["last_price"])
                     for prod, p in self.state["positions"].items())
        return cash + crypto

    # ---------- orders ----------
    def open_position(self, product: str, usd: float, price: float) -> bool:
        if self.dry_run:
            usd = min(usd, self.state["cash_usd"])
            if usd <= 0 or price <= 0:
                return False
            fee = usd * self.fee_pct / 100
            qty = (usd - fee) / price
            self.state["positions"][product] = {
                "qty": qty, "entry_price": price, "peak_price": price,
                "last_price": price, "opened_at": _now(), "cost_usd": usd,
            }
            self.state["cash_usd"] -= usd
            self.state["fees_paid"] += fee
            _log_trade("BUY", product, round(usd, 2), True,
                       f"entry @ {price:.6g} fee {fee:.2f}")
            print(f"  [DRY RUN] BUY  {product}  ${usd:,.2f} @ {price:,.6g}  (fee ${fee:.2f})")
            self.save()
            return True
        _log_trade("BUY", product, round(usd, 2), False, f"entry @ {price:.6g}")
        self.client.market_order_buy(
            client_order_id=str(uuid.uuid4()),
            product_id=product,
            quote_size=str(round(usd, 2)),
        )
        self.state["positions"][product] = {
            "qty": usd / price, "entry_price": price, "peak_price": price,
            "last_price": price, "opened_at": _now(), "cost_usd": usd,
        }
        print(f"  [LIVE] BUY  {product}  ${usd:,.2f} @ {price:,.6g}")
        self.save()
        return True

    def close_position(self, product: str, price: float, reason: str):
        pos = self.state["positions"].get(product)
        if not pos:
            return
        if self.dry_run:
            gross = pos["qty"] * price
            fee = gross * self.fee_pct / 100
            self.state["cash_usd"] += gross - fee
            self.state["fees_paid"] += fee
            pnl = (gross - fee) - pos.get("cost_usd", gross)
            _log_trade("SELL", product, round(gross, 2), True,
                       f"{reason} pnl {pnl:+.2f} fee {fee:.2f}")
            print(f"  [DRY RUN] SELL {product}  ${gross:,.2f} @ {price:,.6g}  "
                  f"({reason}, P&L {pnl:+.2f}, fee ${fee:.2f})")
        else:
            _log_trade("SELL", product, round(pos["qty"] * price, 2), False, reason)
            self.client.market_order_sell(
                client_order_id=str(uuid.uuid4()),
                product_id=product,
                base_size=str(round(pos["qty"], 8)),
            )
            print(f"  [LIVE] SELL {product} @ {price:,.6g}  ({reason})")
        del self.state["positions"][product]
        cooldown = self.cfg["entry"].get("rebuy_cooldown_minutes", 0)
        if cooldown:
            self.state["cooldown_until"][product] = (
                datetime.now(timezone.utc) + timedelta(minutes=cooldown)
            ).isoformat()
        self.save()

    # ---------- equity snapshot (same columns as the parent bot) ----------
    def log_equity_snapshot(self, prices: dict[str, float]):
        equity = self.equity(prices)
        cash = self.state["cash_usd"]
        holdings_value = equity - cash
        start = self.state["start_capital"]
        pnl = equity - start
        new_file = not os.path.exists(EQUITY_FILE)
        with open(EQUITY_FILE, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["timestamp_utc", "equity", "cash", "holdings_value",
                            "pnl", "pnl_pct", "fees_paid"])
            w.writerow([_now(), round(equity, 2), round(cash, 2),
                        round(holdings_value, 2), round(pnl, 2),
                        round(pnl / start * 100, 2) if start else 0,
                        round(self.state["fees_paid"], 2)])
