"""Fetch OHLC candles from Coinbase's public market-data REST API.

The swing strategy only acts on *completed* candles (a candle's close isn't
final until its period ends), so this returns closed candles only — never the
in-progress current one.
"""
import time
from datetime import datetime, timezone, timedelta

import requests

URL = "https://api.exchange.coinbase.com/products/{}/candles"


def fetch_candles(product: str, granularity: int, count: int) -> list[dict]:
    """Return up to `count` most-recent COMPLETED candles, oldest first.

    Each candle is {"t": start_epoch, "o","h","l","c": floats}.
    granularity in seconds (86400 = daily, 14400 = 4h).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=granularity * (count + 3))
    for _ in range(3):
        try:
            r = requests.get(URL.format(product),
                             params={"granularity": granularity,
                                     "start": start.isoformat(), "end": end.isoformat()},
                             timeout=20)
            if r.status_code == 200:
                now = time.time()
                out = []
                for t, lo, hi, op, cl, vol in sorted(r.json()):
                    if t + granularity <= now:          # completed candles only
                        out.append({"t": t, "o": op, "h": hi, "l": lo, "c": cl})
                return out[-count:]
        except Exception:
            pass
        time.sleep(1)
    return []


def fetch_4h(product: str, count: int) -> list[dict]:
    """Coinbase has no native 4h granularity, so aggregate hourly candles into
    completed 4h blocks (aligned to 00:00/04:00/... UTC)."""
    hourly = fetch_candles(product, 3600, count * 4 + 8)
    now = time.time()
    blocks: dict[int, dict] = {}
    for c in hourly:
        b = c["t"] // 14400 * 14400
        x = blocks.get(b)
        if x is None:
            blocks[b] = {"t": b, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"],
                         "first": c["t"], "last": c["t"]}
        else:
            x["h"] = max(x["h"], c["h"])
            x["l"] = min(x["l"], c["l"])
            if c["t"] < x["first"]:
                x["first"] = c["t"]; x["o"] = c["o"]
            if c["t"] >= x["last"]:
                x["last"] = c["t"]; x["c"] = c["c"]
    out = [{"t": x["t"], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"]}
           for b, x in sorted(blocks.items()) if b + 14400 <= now]   # completed blocks only
    return out[-count:]
