"""Real-time trailing stop-loss bot — long-running process.

Subscribes to Coinbase's public ticker feed and, on every price update:
  • while flat: buys a coin that has dipped `dip_pct` below its recent high
    (subject to free capital, max positions and a post-stop cooldown);
  • while holding: trails the peak price and sells on a `trail_pct` drop
    (or an optional take-profit).

Designed to run unattended for weeks. dry_run keeps a simulated portfolio so you
can prove the strategy on paper before risking real money — same discipline as
the daily momentum bot in the parent folder.

Usage:
    python bot.py            # respects dry_run in config.yaml

All ticks are handled on the single feed thread, so trading logic needs no
locking. The only other thread is the feed's staleness watchdog, which just
closes the socket to trigger a reconnect.
"""
import signal
import sys
import time
from collections import deque

import yaml

import strategy
from feed import TickerFeed
from portfolio import Portfolio


class StopLossBot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pf = Portfolio(cfg)
        self.products = cfg["watchlist"]
        self.entry = cfg["entry"]
        self.exit = cfg["exit"]
        self.risk = cfg["risk"]
        self.lookback_s = self.entry["lookback_minutes"] * 60
        # rolling (monotonic_time, price) window per product, for the entry high
        self.windows = {p: deque() for p in self.products}
        self.last_price: dict[str, float] = {}
        self._last_equity_snap = 0.0
        self._last_heartbeat = 0.0

    def _per_position_usd(self) -> float:
        pp = self.risk.get("per_position_usd") or 0
        if pp > 0:
            return pp
        return self.risk["total_capital_usd"] / self.risk["max_positions"]

    def _window_high(self, product: str, now: float) -> float | None:
        win = self.windows[product]
        cutoff = now - self.lookback_s
        while win and win[0][0] < cutoff:
            win.popleft()
        return max((p for _, p in win), default=None)

    # ---------- per-tick handler ----------
    def on_price(self, product: str, price: float):
        now = time.monotonic()
        self.last_price[product] = price
        self.windows[product].append((now, price))
        high = self._window_high(product, now)

        if self.pf.has_position(product):
            self._manage_position(product, price)
        else:
            self._maybe_enter(product, price, high)

        self._periodic(now)

    def _manage_position(self, product: str, price: float):
        pos = self.pf.state["positions"][product]
        if price > pos["peak_price"]:
            pos["peak_price"] = price
        pos["last_price"] = price
        should_exit, reason = strategy.exit_signal(
            price, pos["entry_price"], pos["peak_price"],
            self.exit["trail_pct"], self.exit.get("take_profit_pct", 0),
        )
        if should_exit:
            self.pf.close_position(product, price, reason)

    def _maybe_enter(self, product: str, price: float, high: float | None):
        if self.pf.num_positions() >= self.risk["max_positions"]:
            return
        if self.pf.in_cooldown(product):
            return
        if not strategy.entry_signal(price, high, self.entry["dip_pct"]):
            return
        usd = self._per_position_usd()
        if self.pf.dry_run:
            usd = min(usd, self.pf.state["cash_usd"])
        if usd < self.risk["min_trade_usd"]:
            return
        self.pf.open_position(product, usd, price)

    # ---------- periodic housekeeping ----------
    def _periodic(self, now: float):
        if now - self._last_equity_snap >= self.cfg["log"]["equity_snapshot_seconds"]:
            self._last_equity_snap = now
            self.pf.log_equity_snapshot(self.last_price)
        if now - self._last_heartbeat >= self.cfg["log"]["heartbeat_seconds"]:
            self._last_heartbeat = now
            eq = self.pf.equity(self.last_price)
            held = ", ".join(self.pf.state["positions"].keys()) or "flat"
            print(f"[hb] equity ${eq:,.2f} | positions: {held}")

    # ---------- run ----------
    def run(self):
        feed = TickerFeed(
            self.cfg["feed"]["ws_url"], self.products, self.on_price,
            channel=self.cfg["feed"].get("channel", "ticker"),
            staleness_timeout_s=self.cfg["feed"].get("staleness_timeout_s", 90),
        )

        def shutdown(signum, frame):
            print("\n[bot] shutting down - saving state...")
            feed.stop()
            self.pf.save()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        mode = "DRY RUN (paper)" if self.cfg["dry_run"] else "LIVE TRADING"
        print(f"=== Trailing Stop-Loss Bot | {mode} ===")
        print(f"Watching: {', '.join(self.products)}")
        print(f"Entry: buy a {self.entry['dip_pct']}% dip vs the "
              f"{self.entry['lookback_minutes']}m high  |  "
              f"Exit: {self.exit['trail_pct']}% trailing stop")
        print("(building reference highs - first entries wait for a local peak then a dip)\n")
        feed.run()


def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    StopLossBot(cfg).run()


if __name__ == "__main__":
    main()
