"""Order execution via Coinbase Advanced Trade API.

In dry_run mode, nothing is sent to Coinbase. Instead the executor keeps a
*simulated* portfolio (cash + coin quantities) persisted to a JSON file, so
running the bot day after day builds a real paper track record — including
simulated taker fees — that you can judge before risking actual money.
Every intended trade is also appended to trades_log.csv as an audit trail.
"""
import csv
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

LOG_FILE = "trades_log.csv"
EQUITY_FILE = "equity_history.csv"


def _log_trade(action: str, symbol: str, usd: float, dry_run: bool, note: str = ""):
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp_utc", "mode", "action", "symbol", "usd_amount", "note"])
        w.writerow([
            datetime.now(timezone.utc).isoformat(),
            "DRY_RUN" if dry_run else "LIVE",
            action, symbol, usd, note,
        ])


class Executor:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dry_run = cfg["dry_run"]
        self.quote = cfg["universe"]["quote_currency"]
        self.client = None
        self.paper = None

        if self.dry_run:
            paper_cfg = cfg.get("paper", {})
            self.state_file = paper_cfg.get("state_file", "paper_portfolio.json")
            self.fee_pct = paper_cfg.get("fee_pct", 0.0)
            self.paper = self._load_paper_state()
        else:
            from coinbase.rest import RESTClient  # pip install coinbase-advanced-py
            self.client = RESTClient(
                api_key=os.environ["COINBASE_API_KEY"],
                api_secret=os.environ["COINBASE_API_SECRET"],
            )

    # ---------- paper-portfolio state ----------
    def _load_paper_state(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                return json.load(f)
        capital = self.cfg["risk"]["total_capital_usdc"]
        return {
            "start_capital": capital,
            "cash_usdc": capital,
            "fees_paid": 0.0,
            "holdings": {},  # symbol -> {"qty": float, "last_price": float}
        }

    def _save_paper_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.paper, f, indent=2)

    # ---------- current holdings ----------
    def get_holdings_usd(self, prices: dict[str, float]) -> dict[str, float]:
        """Return {symbol: usd_value} of current crypto holdings."""
        if self.dry_run:
            holdings = {}
            for sym, pos in self.paper["holdings"].items():
                price = prices.get(sym, pos["last_price"])
                pos["last_price"] = price  # remember latest mark for off-list coins
                if pos["qty"] > 0:
                    holdings[sym] = pos["qty"] * price
            return holdings
        holdings = {}
        accounts = self.client.get_accounts()["accounts"]
        for a in accounts:
            sym = a["currency"]
            bal = float(a["available_balance"]["value"])
            if sym in prices and bal > 0:
                holdings[sym] = bal * prices[sym]
        return holdings

    # ---------- equity (for the daily loss circuit breaker) ----------
    def current_equity(self, prices: dict[str, float]) -> float:
        """Total portfolio value right now: cash + crypto marked at current prices."""
        if self.dry_run:
            cash = self.paper["cash_usdc"]
            crypto = sum(
                pos["qty"] * prices.get(sym, pos["last_price"])
                for sym, pos in self.paper["holdings"].items()
            )
            return cash + crypto
        crypto = sum(self.get_holdings_usd(prices).values())
        cash = 0.0
        for a in self.client.get_accounts()["accounts"]:
            if a["currency"] == self.quote:
                cash = float(a["available_balance"]["value"])
        return crypto + cash

    def reference_equity(self, window_hours: float = 24) -> float | None:
        """Equity from ~window_hours ago, for the daily loss circuit breaker.

        Returns the most recent snapshot that is at least window_hours old — a
        true 24h-ago reading, regardless of how many times a day the bot runs.
        Until that much history exists it falls back to the earliest snapshot,
        so the guard still catches an early drop. None only if no history.
        """
        if not os.path.exists(EQUITY_FILE):
            return None
        with open(EQUITY_FILE, newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        aged = [r for r in rows
                if datetime.fromisoformat(r["timestamp_utc"]) <= cutoff]
        ref = aged[-1] if aged else rows[0]
        return float(ref["equity"])

    def ran_today(self) -> bool:
        """True if an equity snapshot has already been recorded today (UTC).

        Used to enforce one rebalance per day: the first run of the day sees no
        today-row yet and trades; later runs see it and skip trading.
        """
        if not os.path.exists(EQUITY_FILE):
            return False
        today = datetime.now(timezone.utc).date().isoformat()
        with open(EQUITY_FILE, newline="") as f:
            return any(r["timestamp_utc"].startswith(today)
                       for r in csv.DictReader(f))

    def mark_price(self, symbol: str, prices: dict[str, float]) -> float | None:
        """Best available price for a coin: the current scan price, or — when a
        holding has dropped out of the top-N scan — the last price we marked it
        at. Prevents a KeyError when selling a coin that left the universe."""
        if symbol in prices:
            return prices[symbol]
        if self.dry_run:
            pos = self.paper["holdings"].get(symbol)
            if pos:
                return pos["last_price"]
        return None

    # ---------- orders ----------
    def market_buy(self, symbol: str, usd: float, price: float | None = None):
        if self.dry_run:
            # Can't spend more cash than the simulated portfolio holds.
            usd = min(usd, self.paper["cash_usdc"])
            if usd <= 0 or not price:
                return
            fee = usd * self.fee_pct / 100
            qty = (usd - fee) / price  # coins received after fee
            pos = self.paper["holdings"].setdefault(symbol, {"qty": 0.0, "last_price": price})
            pos["qty"] += qty
            pos["last_price"] = price
            self.paper["cash_usdc"] -= usd
            self.paper["fees_paid"] += fee
            _log_trade("BUY", symbol, round(usd, 2), True, f"fee {fee:.2f}")
            print(f"  [DRY RUN] BUY  {symbol}  ${usd:,.2f}  (fee ${fee:.2f})")
            return
        _log_trade("BUY", symbol, usd, False)
        self.client.market_order_buy(
            client_order_id=str(uuid.uuid4()),
            product_id=f"{symbol}-{self.quote}",
            quote_size=str(round(usd, 2)),
        )
        print(f"  [LIVE] BUY  {symbol}  ${usd:,.2f}")

    def market_sell(self, symbol: str, usd: float, price: float):
        if self.dry_run:
            pos = self.paper["holdings"].get(symbol)
            if pos:
                qty_to_sell = min(usd / price, pos["qty"])
                gross = qty_to_sell * price
                fee = gross * self.fee_pct / 100
                pos["qty"] -= qty_to_sell
                pos["last_price"] = price
                self.paper["cash_usdc"] += gross - fee
                self.paper["fees_paid"] += fee
                if pos["qty"] <= 1e-9:
                    del self.paper["holdings"][symbol]
                _log_trade("SELL", symbol, round(gross, 2), True, f"fee {fee:.2f}")
                print(f"  [DRY RUN] SELL {symbol}  ${gross:,.2f}  (fee ${fee:.2f})")
            return
        _log_trade("SELL", symbol, usd, False)
        base_size = round(usd / price, 8)
        self.client.market_order_sell(
            client_order_id=str(uuid.uuid4()),
            product_id=f"{symbol}-{self.quote}",
            base_size=str(base_size),
        )
        print(f"  [LIVE] SELL {symbol}  ${usd:,.2f}")

    # ---------- paper P&L report ----------
    def report_paper_pnl(self, prices: dict[str, float]):
        """Print simulated portfolio value vs. starting capital, then persist state."""
        if not self.dry_run:
            return

        cash = self.paper["cash_usdc"]
        holdings_value = 0.0
        lines = []
        for sym, pos in sorted(self.paper["holdings"].items()):
            price = prices.get(sym, pos["last_price"])
            val = pos["qty"] * price
            holdings_value += val
            lines.append(f"  {sym:<6} {pos['qty']:.6f} @ ${price:,.2f} = ${val:,.2f}")

        equity = cash + holdings_value
        start = self.paper["start_capital"]
        pnl = equity - start

        print("\nPaper portfolio (simulated):")
        print(f"  Cash {self.quote}: ${cash:,.2f}")
        for line in lines:
            print(line)
        print(f"  {'-' * 44}")
        print(f"  Total equity:     ${equity:,.2f}")
        print(f"  Starting capital: ${start:,.2f}")
        print(f"  Paper P&L:        {pnl:+,.2f} ({pnl / start * 100:+.2f}%)")
        print(f"  Simulated fees paid to date: ${self.paper['fees_paid']:,.2f}")

        self._save_paper_state()
        self._log_equity_snapshot(equity, cash, holdings_value, pnl, start)

    def _log_equity_snapshot(self, equity, cash, holdings_value, pnl, start):
        """Append one row per run so report.py can chart the equity curve."""
        new_file = not os.path.exists(EQUITY_FILE)
        with open(EQUITY_FILE, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["timestamp_utc", "equity", "cash",
                            "holdings_value", "pnl", "pnl_pct", "fees_paid"])
            w.writerow([
                datetime.now(timezone.utc).isoformat(),
                round(equity, 2), round(cash, 2), round(holdings_value, 2),
                round(pnl, 2), round(pnl / start * 100, 2) if start else 0,
                round(self.paper["fees_paid"], 2),
            ])
