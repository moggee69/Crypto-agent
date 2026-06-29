"""Entry/exit decision logic for the trailing stop-loss bot (pure functions).

Entry — buy the dip: when flat, buy once price has fallen `dip_pct` below the
        highest price seen over the recent `lookback_minutes` window.
Exit  — trailing stop: track the peak price since entry and sell when price
        falls `trail_pct` below that peak. An optional fixed take-profit from
        the entry price can also trigger an exit.

Keeping these as side-effect-free functions makes the rules trivial to reason
about and unit-test in isolation from the websocket and the portfolio.
"""


def entry_signal(price: float, window_high: float | None, dip_pct: float) -> bool:
    """True if price is at least `dip_pct` below the recent window high."""
    if not window_high or window_high <= 0:
        return False
    drawdown_pct = (window_high - price) / window_high * 100
    return drawdown_pct >= dip_pct


def trend_ok(price: float, moving_avg: float | None) -> bool:
    """Trend gate for entries: only buy a dip when the coin is also trending up,
    i.e. the current price is above its moving average. This keeps the bot out of
    "falling knife" dip-buys during a downtrend — the single change that most
    improved the strategy in backtests across both choppy and trending markets.

    `moving_avg` is None until the average has warmed up; we stay out (return
    False) rather than guess on missing data.
    """
    if moving_avg is None:
        return False
    return price > moving_avg


def exit_signal(price: float, entry_price: float, peak_price: float,
                trail_pct: float, take_profit_pct: float = 0) -> tuple[bool, str]:
    """Return (should_exit, reason)."""
    if take_profit_pct and price >= entry_price * (1 + take_profit_pct / 100):
        return True, "take-profit"
    if peak_price > 0 and price <= peak_price * (1 - trail_pct / 100):
        return True, "trailing-stop"
    return False, ""
