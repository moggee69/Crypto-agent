"""Append-only minute-bar logger — builds a growing local price database.

The bot receives a stream of ticks; this aggregates them into one OHLC bar per
minute per product and appends completed bars to per-product CSV files. Combined
with a one-time backfill (backfill_minute_data.py) it builds a minute-resolution
dataset from when logging started, for later analysis.

Files:  <dir>/<PRODUCT>.csv   columns: minute_utc,open,high,low,close
A bar is written when its minute completes (first tick of the next minute) and
on shutdown. A per-product "last written minute" guard prevents duplicate rows
across restarts and backfill overlap, so the file stays clean and in order.
"""
import csv
import os
from datetime import datetime, timezone


class MinuteLogger:
    def __init__(self, directory: str, products: list[str]):
        self.dir = directory
        os.makedirs(self.dir, exist_ok=True)
        self.bar: dict[str, list] = {}          # product -> [minute, o, h, l, c]
        self.last_written: dict[str, int] = {}   # product -> last minute epoch written
        for p in products:
            self.last_written[p] = self._last_minute_in_file(p)

    def _path(self, product: str) -> str:
        return os.path.join(self.dir, f"{product}.csv")

    def _last_minute_in_file(self, product: str) -> int:
        """Efficiently read the last logged minute (reads only the file tail)."""
        path = self._path(product)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return -1
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "ignore").splitlines()
        for line in reversed(tail):
            ts = line.split(",", 1)[0]
            if ts and ts != "minute_utc":
                try:
                    return int(datetime.fromisoformat(ts).timestamp())
                except ValueError:
                    continue
        return -1

    def _flush(self, product: str):
        bar = self.bar.get(product)
        if not bar:
            return
        minute, o, h, l, c = bar
        if minute <= self.last_written.get(product, -1):
            return
        path = self._path(product)
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["minute_utc", "open", "high", "low", "close"])
            w.writerow([datetime.fromtimestamp(minute, timezone.utc).isoformat(), o, h, l, c])
        self.last_written[product] = minute

    def on_tick(self, product: str, wall: float, price: float):
        minute = int(wall // 60) * 60
        bar = self.bar.get(product)
        if bar is None or bar[0] != minute:
            if bar is not None:
                self._flush(product)                       # previous minute is now complete
            self.bar[product] = [minute, price, price, price, price]
        else:
            bar[2] = max(bar[2], price)                     # high
            bar[3] = min(bar[3], price)                     # low
            bar[4] = price                                  # close

    def flush_all(self):
        for p in list(self.bar):
            self._flush(p)
