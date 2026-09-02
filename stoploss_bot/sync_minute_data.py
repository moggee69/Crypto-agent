"""Minute-bar price logger for every coin the live bots trade — REST + cron.

Successor to the tick-based minute_logger.py (which died when the old stop-loss
bot was retired on 2026-08-28). This fetches 1-minute OHLCV candles straight from
Coinbase for the union of 002's and 003's watchlists (read live from their config
files, so it tracks whatever the bots trade) and merges them into per-product CSVs
under minute_data/ — the exact same format the old logger wrote:

    minute_utc,open,high,low,close,volume

Dedups by minute and rewrites each file sorted, so every run is safe/idempotent
and safe to run alongside the old June–Aug history.

Usage:
    python sync_minute_data.py                # rolling recent window (for the cron)
    python sync_minute_data.py 2026-08-28     # one-time backfill from a UTC date
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA_DIR = os.path.join(HERE, "minute_data")
CANDLES = "https://api.exchange.coinbase.com/products/{}/candles"
RECENT_MINUTES = 180                       # rolling window when no start date is given
CONFIGS = ("swing_bot/config.yaml", "agent003/config.yaml")


def coins() -> list[str]:
    """Union of the live bots' watchlists (002 + 003), in first-seen order."""
    out: list[str] = []
    for rel in CONFIGS:
        try:
            wl = yaml.safe_load(open(os.path.join(REPO, rel))).get("watchlist", []) or []
        except OSError:
            wl = []
        for c in wl:
            if c not in out:
                out.append(c)
    return out


def _load(path: str) -> dict:
    bars = {}
    if os.path.exists(path):
        with open(path) as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) >= 5:
                    try:
                        bars[int(datetime.fromisoformat(row[0]).timestamp())] = (row[1:6] + [""])[:5]
                    except ValueError:
                        pass
    return bars


def sync(product: str, start: datetime, directory: str) -> tuple[int, int]:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{product}.csv")
    bars = _load(path)
    added = 0
    cur, end = start, datetime.now(timezone.utc)
    while cur < end:
        ce = min(cur + timedelta(minutes=300), end)
        for _ in range(3):
            try:
                r = requests.get(CANDLES.format(product),
                                 params={"granularity": 60,
                                         "start": cur.isoformat(), "end": ce.isoformat()},
                                 timeout=30)
                if r.status_code == 200:
                    for t, lo, hi, op, cl, vol in r.json():
                        if t not in bars:
                            bars[t] = [op, hi, lo, cl, vol]
                            added += 1
                        elif bars[t][4] in ("", None):
                            bars[t][4] = vol        # backfill volume on existing rows
                    break
            except Exception:
                pass
            time.sleep(0.8)
        cur = ce
        time.sleep(0.15)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["minute_utc", "open", "high", "low", "close", "volume"])
        for t in sorted(bars):
            o, h, l, c, vol = bars[t]
            w.writerow([datetime.fromtimestamp(t, timezone.utc).isoformat(), o, h, l, c, vol])
    return len(bars), added


def main():
    if len(sys.argv) > 1:
        start = datetime.fromisoformat(sys.argv[1]).replace(tzinfo=timezone.utc)
    else:
        start = datetime.now(timezone.utc) - timedelta(minutes=RECENT_MINUTES)
    cs = coins()
    print(f"sync {len(cs)} coins from {start.isoformat()} -> now")
    for p in cs:
        total, added = sync(p, start, DATA_DIR)
        print(f"  {p:<10} {total} rows (+{added} new)")
    print("done.")


if __name__ == "__main__":
    main()
