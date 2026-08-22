"""Shared risk guards for the always-on bots (stoploss_bot, swing_bot).

Ports the ideas behind Freqtrade's "protections" + pairlist filters into a small,
side-effect-free module both bots import. Everything here only ever *prevents*
an entry — it can never open, close, or size a position — so enabling it can only
make a bot more cautious, never introduce a bad trade.

Three guards, all config-driven and individually toggleable:

  StoplossGuard   — after N losing exits inside a rolling window, stop opening
                    new positions until those losses age out of the window.
  MaxDrawdown     — while equity sits >= X% below its all-time peak, stop opening
                    new positions (a portfolio circuit-breaker).
  liquidity gate  — skip an entry when the coin's 24h USD volume is too thin or
                    its bid/ask spread is too wide (avoids illiquid fills).

The bots are single-host and co-located under /opt/crypto-agent, so both import
this from ../shared. The root momentum bot runs elsewhere (GitHub Actions) and
is intentionally out of scope.
"""
import time

import requests

STATS = "https://api.exchange.coinbase.com/products/{}/stats"
TICKER = "https://api.exchange.coinbase.com/products/{}/ticker"

_LIQ_CACHE: dict[str, tuple[float, bool, str]] = {}   # product -> (expiry_epoch, ok, reason)
_LIQ_TTL_S = 300


class RiskGuard:
    """Holds the StoplossGuard + MaxDrawdown protections. Fed each losing exit via
    `record_exit`; asked `entry_block_reason` before every would-be entry."""

    def __init__(self, protections_cfg: dict | None):
        self.cfg = protections_cfg or {}
        self.exits: list[tuple[float, float]] = []   # (epoch, pnl_usd) recent exits

    # ---- exit history (feeds StoplossGuard) ----
    def record_exit(self, ts: float, pnl: float):
        self.exits.append((ts, pnl))
        # keep only what any guard could still look at (bounded memory). Trim
        # relative to the most recent exit, not wall-clock, so the window is
        # self-consistent regardless of the clock source the caller passes.
        horizon = max(self._sg().get("lookback_hours", 0) * 3600, 3600)
        latest = max(t for t, _ in self.exits)
        self.exits = [(t, p) for t, p in self.exits if t >= latest - horizon]

    def _sg(self) -> dict:
        return self.cfg.get("stoploss_guard", {}) or {}

    def _dd(self) -> dict:
        return self.cfg.get("max_drawdown", {}) or {}

    # ---- guards ----
    def stoploss_halt(self, now: float) -> str | None:
        g = self._sg()
        if not g.get("enabled"):
            return None
        window = g.get("lookback_hours", 24) * 3600
        thr = g.get("required_profit_usd", 0.0)
        losers = [p for t, p in self.exits if now - t <= window and p <= thr]
        if len(losers) >= g.get("trade_limit", 3):
            return (f"StoplossGuard: {len(losers)} losing exits in "
                    f"{g.get('lookback_hours', 24)}h (limit {g.get('trade_limit', 3)})")
        return None

    def drawdown_check(self, now: float, equity_now: float, peak_equity: float,
                       halt_until: float) -> tuple[bool, str | None, float, float]:
        """Pause-and-resume circuit breaker. Returns
        (blocked, reason, new_halt_until, new_peak) — the caller persists the last
        two into its state.

        When equity falls `max_drawdown_pct` below its high-water mark, entries
        pause for `cooldown_hours`. When that cooldown elapses the peak baseline is
        RESET to current equity, so the bot resumes from a fresh high-water mark
        instead of being locked out forever (the old bug: an all-time peak that
        never reset meant one bad run bricked the bot permanently)."""
        g = self._dd()
        peak = max(peak_equity or 0.0, equity_now)
        if not g.get("enabled"):
            return (False, None, 0.0, peak)
        limit = g.get("max_drawdown_pct", 20)
        cooldown_h = g.get("cooldown_hours", 12)
        if halt_until:
            if now < halt_until:
                return (True, f"MaxDrawdown cooldown — {int((halt_until - now) / 60)}m left",
                        halt_until, peak)
            return (False, None, 0.0, equity_now)         # cooldown done: reset baseline, resume
        if peak > 0:
            dd = (peak - equity_now) / peak * 100
            if dd >= limit:
                return (True, f"MaxDrawdown {dd:.1f}% >= {limit}% — pausing {cooldown_h}h",
                        now + cooldown_h * 3600, peak)
        return (False, None, halt_until, peak)


def liquidity_ok(product: str, cfg: dict | None) -> tuple[bool, str]:
    """Volume + spread pairlist filter. Returns (ok, reason_if_not).

    Fails OPEN: if the stats/ticker call errors we allow the trade rather than
    let a transient API hiccup silently stop the bot trading. Only called when a
    buy is otherwise imminent, so the two REST calls are rare, not per-tick."""
    g = cfg or {}
    if not g.get("enabled"):
        return True, ""
    cached = _LIQ_CACHE.get(product)
    if cached and cached[0] > time.time():
        return cached[1], cached[2]
    try:
        s = requests.get(STATS.format(product), timeout=10).json()
        t = requests.get(TICKER.format(product), timeout=10).json()
        last = float(t.get("price") or s.get("last") or 0)
        vol_usd = float(s.get("volume") or 0) * last          # 24h base volume * price
        bid, ask = float(t.get("bid") or 0), float(t.get("ask") or 0)
        spread_pct = (ask - bid) / ask * 100 if ask > 0 else 999.0
    except Exception:
        return True, ""                                        # fail open
    min_vol = g.get("min_24h_volume_usd", 0)
    max_spread = g.get("max_spread_pct")
    if min_vol and vol_usd < min_vol:
        result = (False, f"thin 24h volume ${vol_usd:,.0f} < ${min_vol:,.0f}")
    elif max_spread is not None and spread_pct > max_spread:
        result = (False, f"wide spread {spread_pct:.2f}% > {max_spread}%")
    else:
        result = (True, "")
    _LIQ_CACHE[product] = (time.time() + _LIQ_TTL_S, *result)
    return result
