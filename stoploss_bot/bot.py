"""Real-time trailing stop-loss bot — long-running process.

Subscribes to Coinbase's public ticker feed and, on every price update:
  • while flat: buys a coin that has dipped `dip_pct` below its recent high
    AND is trending up (price above its moving average — the trend filter),
    subject to free capital, max positions and a post-stop cooldown;
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
import os
import signal
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import yaml

import strategy
import universe
from feed import TickerFeed
from minute_logger import MinuteLogger
from portfolio import Portfolio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import risk_guard  # noqa: E402  (shared module, resolved via the path insert above)

EXCHANGE_CANDLES = "https://api.exchange.coinbase.com/products/{}/candles"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class StopLossBot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pf = Portfolio(cfg)
        self.products = cfg["watchlist"]
        self.entry = cfg["entry"]
        self.exit = cfg["exit"]
        self.risk = cfg["risk"]
        self.lookback_minutes = self.entry["lookback_minutes"]
        self.lookback_s = self.lookback_minutes * 60
        # rolling (epoch_time, price) window per product, for the entry high.
        # Epoch (not monotonic) time so it can be pre-seeded from REST history.
        # defaultdicts so a coin entering a dynamic universe self-initialises on
        # its first tick (CPython dict ops are atomic under the GIL, so the
        # refresh thread adding keys is safe alongside the feed thread).
        self.windows = defaultdict(deque)
        self.last_price: dict[str, float] = {}

        # Trend filter: a per-product moving average sampled once per minute.
        self.trend_cfg = self.entry.get("trend_filter", {})
        self.trend_enabled = bool(self.trend_cfg.get("enabled", False))
        self.ma_minutes = int(self.trend_cfg.get("ma_hours", 12) * 60)
        self.ma_samples = defaultdict(deque)                    # (minute_epoch, price)
        self.ma_sum = defaultdict(float)
        self.last_ma_minute: dict[str, int | None] = defaultdict(lambda: None)

        # Trading universe. "fixed" = trade the watchlist; "dynamic_trending" =
        # trade the top-N trending Coinbase pairs, refreshed periodically. The
        # watchlist stays the minute-logging set (keeps the price DB stable) and
        # the fallback if universe selection ever fails.
        self.uni_cfg = cfg.get("universe", {})
        self.dynamic = self.uni_cfg.get("mode") == "dynamic_trending"
        self.data_log_products = set(self.products)
        self.universe: set[str] = set(self.products)            # dynamic mode replaces this on first refresh
        self.feed = None
        self._seeded: set[str] = set()
        self._stop_refresh = False

        # Risk guards (StoplossGuard + MaxDrawdown) and the liquidity pairlist
        # filter — all optional, config-driven, and entry-only (never force a trade).
        self.guard = risk_guard.RiskGuard(cfg.get("protections"))
        self.liquidity_cfg = cfg.get("liquidity")

        # Minute-bar logger — builds a growing local price database (optional).
        # Always logs the fixed watchlist, independent of the trading universe.
        self.data_cfg = cfg.get("data_log", {})
        self.minute_logger = (
            MinuteLogger(self.data_cfg.get("dir", "minute_data"), self.products)
            if self.data_cfg.get("enabled", False) else None
        )

        self._last_equity_snap = 0.0
        self._last_heartbeat = 0.0

    def _per_position_usd(self) -> float:
        pp = self.risk.get("per_position_usd") or 0
        if pp > 0:
            return pp
        return self.risk["total_capital_usd"] / self.risk["max_positions"]

    # ---------- trend moving average ----------
    def _update_ma(self, product: str, wall: float, price: float):
        """Record at most one price sample per minute and keep a running mean
        over the trailing `ma_minutes` window."""
        if not self.trend_enabled:
            return
        minute = int(wall // 60) * 60
        if self.last_ma_minute[product] == minute:
            return
        self.last_ma_minute[product] = minute
        dq = self.ma_samples[product]
        dq.append((minute, price))
        self.ma_sum[product] += price
        cutoff = minute - self.ma_minutes * 60
        while dq and dq[0][0] < cutoff:
            self.ma_sum[product] -= dq.popleft()[1]

    def _moving_avg(self, product: str) -> float | None:
        """Current trend MA, or None until it has warmed up to ~80% of the
        window (so a fresh start doesn't act on a half-built average)."""
        dq = self.ma_samples[product]
        if not dq or dq[-1][0] - dq[0][0] < 0.8 * self.ma_minutes * 60:
            return None
        return self.ma_sum[product] / len(dq)

    # ---------- per-tick handler ----------
    def on_price(self, product: str, price: float, size: float = 0.0,
                 now: float | None = None):
        wall = time.time() if now is None else now
        self.last_price[product] = price

        if self.minute_logger and product in self.data_log_products:
            self.minute_logger.on_tick(product, wall, price, size)

        win = self.windows[product]
        win.append((wall, price))
        cutoff = wall - self.lookback_s
        while win and win[0][0] < cutoff:
            win.popleft()
        high = max(p for _, p in win)

        self._update_ma(product, wall, price)

        if self.pf.has_position(product):
            self._manage_position(product, price)
        else:
            self._maybe_enter(product, price, high)

        self._periodic(time.monotonic())

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
            pnl = self.pf.close_position(product, price, reason)
            if pnl is not None:
                self.guard.record_exit(time.time(), pnl)   # feeds the StoplossGuard

    def _maybe_enter(self, product: str, price: float, high: float | None):
        if product not in self.universe:      # only OPEN new positions in the current universe
            return
        if self.pf.num_positions() >= self.risk["max_positions"]:
            return
        if self.pf.in_cooldown(product):
            return
        # Trend gate: only buy dips when the coin is trending up.
        if self.trend_enabled and not strategy.trend_ok(price, self._moving_avg(product)):
            return
        if not strategy.entry_signal(price, high, self.entry["dip_pct"]):
            return
        # A buy is otherwise imminent — apply the risk guards last (the two below
        # are the only place they run, so their cost stays off the per-tick path).
        blocked = self.guard.entry_block_reason(
            time.time(), self.pf.equity(self.last_price),
            self.pf.state.get("peak_equity", self.pf.state["start_capital"]))
        if blocked:
            print(f"  [guard] entry halted: {blocked}")
            return
        ok, why = risk_guard.liquidity_ok(product, self.liquidity_cfg)
        if not ok:
            print(f"  [guard] skip {product}: {why}")
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

    # ---------- startup warm-up ----------
    def _seed_products(self, products):
        """Best-effort: pre-fill the dip window and trend MA from recent 1-min
        REST candles so a coin is effective as soon as it's traded instead of
        waiting ~the MA window to warm up. Used at startup and whenever a new coin
        enters the dynamic universe. Safe to fail — it then warms from the feed."""
        try:
            import requests
        except Exception:
            print("[seed] requests unavailable - warming from the live feed instead")
            return
        need_min = self.lookback_minutes + 5
        if self.trend_enabled:
            need_min = max(need_min, self.ma_minutes + 5)
        for p in products:
            try:
                end = time.time()
                cur = end - need_min * 60
                candles = {}
                while cur < end:
                    ce = min(cur + 300 * 60, end)
                    r = requests.get(EXCHANGE_CANDLES.format(p),
                                     params={"granularity": 60,
                                             "start": _iso(cur), "end": _iso(ce)},
                                     timeout=20)
                    if r.status_code == 200:
                        for t, lo, hi, op, cl, vol in r.json():
                            candles[t] = cl
                    cur = ce
                for t in sorted(candles):
                    pr = candles[t]
                    self.windows[p].append((t, pr))
                    self._update_ma(p, t, pr)
                cutoff = time.time() - self.lookback_s
                while self.windows[p] and self.windows[p][0][0] < cutoff:
                    self.windows[p].popleft()
                state = "MA ready" if self._moving_avg(p) else "MA warming"
                print(f"[seed] {p}: {len(candles)} candles ({state})")
            except Exception as e:
                print(f"[seed] {p}: skipped ({e})")
            self._seeded.add(p)

    # ---------- dynamic universe ----------
    def _held(self) -> set[str]:
        """Currently-held products, snapshotted safely against the feed thread."""
        for _ in range(3):
            try:
                return set(self.pf.state["positions"].keys())
            except RuntimeError:                      # dict changed size mid-iteration
                time.sleep(0.05)
        return set()

    def _subscribe_set(self) -> set[str]:
        # Stay subscribed to: the trading universe + anything we still hold (so
        # dropped-out positions are still managed to exit) + the data-log watchlist.
        return set(self.universe) | self._held() | self.data_log_products

    def _compute_universe(self) -> set[str]:
        """Re-pick the trending universe, seed any newly-added coins, and return
        the full product set to stay subscribed to."""
        top = universe.select_trending(self.cfg, list(self.products))
        self.universe = {d["id"] for d in top}
        fresh = [c for c in self.universe if c not in self._seeded]
        if fresh:
            self._seed_products(fresh)
        print("[universe] top {}: {}".format(len(top),
              ", ".join(f"{d['id']}({d['gain']:+.0f}%)" for d in top)), flush=True)
        return self._subscribe_set()

    def _refresh_loop(self):
        interval = self.uni_cfg.get("refresh_minutes", 120) * 60
        while not self._stop_refresh:
            slept = 0
            while slept < interval and not self._stop_refresh:
                time.sleep(min(5, interval - slept))
                slept += 5
            if self._stop_refresh:
                break
            try:
                subscribe = self._compute_universe()
                if self.feed:
                    added, removed = self.feed.set_products(sorted(subscribe))
                    if added or removed:
                        print(f"[universe] feed +{len(added)} -{len(removed)} "
                              f"(now {len(subscribe)} subscribed)", flush=True)
            except Exception as e:
                print(f"[universe] refresh error: {e}", flush=True)

    # ---------- run ----------
    def run(self):
        if self.dynamic:
            print("Selecting initial trending universe...")
            init_products = sorted(self._compute_universe())
        else:
            init_products = list(self.products)

        feed = TickerFeed(
            self.cfg["feed"]["ws_url"], init_products, self.on_price,
            channel=self.cfg["feed"].get("channel", "ticker"),
            staleness_timeout_s=self.cfg["feed"].get("staleness_timeout_s", 90),
        )
        self.feed = feed

        def shutdown(signum, frame):
            print("\n[bot] shutting down - saving state...")
            self._stop_refresh = True
            feed.stop()
            if self.minute_logger:
                self.minute_logger.flush_all()
            self.pf.save()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        mode = "DRY RUN (paper)" if self.cfg["dry_run"] else "LIVE TRADING"
        print(f"=== Trending Volatile Agent 001  ·  trending top-10 / trailing stop  |  {mode} ===")
        if self.dynamic:
            print(f"Universe: DYNAMIC top-{self.uni_cfg.get('size', 10)} trending "
                  f"(rank {self.uni_cfg.get('rank_by', 'gain_24h')}, "
                  f"vol >= ${self.uni_cfg.get('min_24h_volume_usd', 0):,.0f}, "
                  f"refresh {self.uni_cfg.get('refresh_minutes', 120)}m)")
            print(f"Now trading: {', '.join(sorted(self.universe))}")
            print(f"Also logging watchlist: {', '.join(sorted(self.data_log_products))}")
        else:
            print(f"Watching: {', '.join(self.products)}")
        trend = (f"trend filter ON (price > {self.trend_cfg.get('ma_hours', 12)}h MA)"
                 if self.trend_enabled else "trend filter OFF")
        print(f"Entry: buy a {self.entry['dip_pct']}% dip vs the "
              f"{self.lookback_minutes}m high, {trend}  |  "
              f"Exit: {self.exit['trail_pct']}% trailing stop")
        if not self.dynamic and self.trend_enabled:
            print("Warming up from recent history...")
            self._seed_products(self.products)
        if self.minute_logger:
            print(f"Minute-data logging ON -> {self.data_cfg.get('dir', 'minute_data')}/")
        if self.dynamic:
            threading.Thread(target=self._refresh_loop, daemon=True).start()
            print(f"Universe refresh thread started (every {self.uni_cfg.get('refresh_minutes', 120)}m).")
        print()
        feed.run()


def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    StopLossBot(cfg).run()


if __name__ == "__main__":
    main()
