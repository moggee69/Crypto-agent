"""One-time backfill of minute bars, so the local price database includes history
from before the bot started logging.

Fetches 1-minute candles from Coinbase for each watchlist product from a start
date to now, and merges them into the same per-product CSV files the live logger
appends to (<data_log.dir>/<PRODUCT>.csv). Deduplicates by minute and rewrites
each file sorted, so it's safe to re-run and safe to run alongside existing data.

Run it once on the server BEFORE restarting the bot with logging enabled:
    ./venv/bin/python backfill_minute_data.py 2026-06-28     # start date (UTC)
    ./venv/bin/python backfill_minute_data.py                 # defaults to 2026-06-28
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
import yaml

CANDLES = "https://api.exchange.coinbase.com/products/{}/candles"
DEFAULT_START = "2026-06-28"   # ~when the stop-loss bot went live


def _load(path: str) -> dict:
    bars = {}
    if os.path.exists(path):
        with open(path) as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) >= 5:
                    try:
                        bars[int(datetime.fromisoformat(row[0]).timestamp())] = row[1:5]
                    except ValueError:
                        pass
    return bars


def backfill(product: str, start: datetime, directory: str):
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
                            bars[t] = [op, hi, lo, cl]
                            added += 1
                    break
            except Exception:
                pass
            time.sleep(0.8)
        cur = ce
        time.sleep(0.15)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["minute_utc", "open", "high", "low", "close"])
        for t in sorted(bars):
            o, h, l, c = bars[t]
            w.writerow([datetime.fromtimestamp(t, timezone.utc).isoformat(), o, h, l, c])
    return len(bars), added


def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    products = cfg["watchlist"]
    directory = cfg.get("data_log", {}).get("dir", "minute_data")
    start_str = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
    start = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
    print(f"Backfilling minute bars from {start.date()} -> now into {directory}/")
    for p in products:
        total, added = backfill(p, start, directory)
        print(f"  {p:<10} {total} rows total (+{added} new)")
    print("Done.")


if __name__ == "__main__":
    main()
