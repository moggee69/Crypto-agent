"""Pure decision helpers for the swing strategy.

Kept side-effect-free so the exact same logic can be replayed over history to
validate it, and run live in the bot.

  BUY  = after 2-4 red daily candles, a green candle (the turn). If the fall
         runs past `extended_fall_days`, buy the first green 4h candle instead.
         Optional insurance: only buy when price is above its long MA (uptrend).
  SELL = daily close drops below its short MA (`ma_exit_days`) — a trend break.
"""


def is_green(candle: dict) -> bool:
    return candle["c"] >= candle["o"]


def red_run_ending(daily: list[dict], idx: int) -> int:
    """Count consecutive red candles ending at index `idx` (inclusive)."""
    n = 0
    while idx >= 0 and daily[idx]["c"] < daily[idx]["o"]:
        n += 1
        idx -= 1
    return n


def sma(daily: list[dict], n: int) -> float | None:
    """Simple moving average of the last n closes, or None until enough data."""
    if len(daily) < n:
        return None
    return sum(d["c"] for d in daily[-n:]) / n


def insurance_ok(ins_cfg: dict, ma_long: float | None, price: float) -> bool:
    """Uptrend filter: True if disabled, or price is above its long MA."""
    if not ins_cfg.get("enabled", False):
        return True
    return ma_long is not None and price > ma_long
