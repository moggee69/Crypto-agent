"""Decision helpers for the MACD/RSI swing strategy (pure, side-effect-free).

BUY  = MACD line crosses ABOVE its signal line (momentum turns up) while RSI is
       below `rsi_buy_max` — buying a dip that's turning, not chasing strength.
SELL = once RSI has been at/above `rsi_overbought` since entry (the position is
       "armed"), sell when price fades `trail_pct` from its peak. No downside
       stop — it holds through pullbacks and only exits after a run overheats.

Full-history backtests (2019+, through the 2021 bull and 2022 bear) picked
RSI<40 / overbought 75 / 10% fade as the robust setting: buy real dips, ride
the run a long time, exit only when it goes hot and then rolls over.
"""


def ema(vals, n):
    k = 2 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def macd_lines(closes, fast=12, slow=26, sig=9):
    """Return (macd_line, signal_line) or (None, None) until there's enough data."""
    if len(closes) < slow + sig:
        return None, None
    macd = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    return macd, ema(macd, sig)


def rsi(closes, n=14):
    """Wilder's RSI. Returns a list aligned to `closes` (None until warmed up)."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    g = sum(max(closes[i] - closes[i - 1], 0) for i in range(1, n + 1)) / n
    l = sum(max(closes[i - 1] - closes[i], 0) for i in range(1, n + 1)) / n
    out[n] = 100 - 100 / (1 + (g / l if l else 999))
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + (g / l if l else 999))
    return out


def macd_bull_cross(macd, signal):
    """True if the MACD line crossed above its signal line on the latest bar."""
    return (macd is not None and signal is not None and len(macd) >= 2
            and macd[-2] <= signal[-2] and macd[-1] > signal[-1])
