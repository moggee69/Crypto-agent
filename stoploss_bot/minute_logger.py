"""Append-only minute-bar logger — builds a growing local price database.

The bot receives a stream of ticks; this aggregates them into one OHLC bar per
minute per product and appends completed bars to per-product CSV files. Combined
with a one-time backfill (backfill_minute_data.py) it builds a minute-resolution
dataset from when logging started, for later analysis.

Files:  <dir>/<PRODUCT>.csv   columns: minute_utc,open,high,low,close,volume
A bar is written when its minute completes (first tick of the next minute) and
on shutdown. A per-product "last written minute" guard prevents duplicate rows
across restarts and backfill overlap, so the file stays clean and in order.

Volume is the sum of trade sizes (base units) seen on the ticker feed during the
minute — a proxy for traded volume. Rows logged before volume support was added
have an empty volume field; a one-time migration adds the header column on start.
"""
import csv
import os
from datetime import datetime, timezone

HEADER = ["minute_utc", "open", "high", "low", "close", "volume"]


class MinuteLogger:
    def __init__(self, directory: str, products: list[str]):
        self.dir = directory
        os.makedirs(self.dir, exist_ok=True)
        self.bar: dict[str, list] = {}          # product -> [minute, o, h, l, c, volume]
        self.last_written: dict[str, int] = {}   # product -> last minute epoch written
        for p in products:
            self._migrate(p)
            self.last_written[p] = self._last_minute_in_file(p)

    def _path(self, product: str) -> str:
        return os.path.join(self.dir, f"{product}.csv")

    def _migrate(self, product: str):
        """One-time: add the `volume` column to files written before volume support.
        Old rows get an empty volume field so the file stays a consistent 6 columns."""
        path = self._path(product)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return
        with open(path) as f:
            header = f.readline()
        if "volume" in header:
            return                                          # already migrated
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            for row in rows[1:]:
                if row:
                    w.writerow((row + [""])[:6])            # pad old 5-col rows

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
        minute, o, h, l, c, vol = bar
        if minute <= self.last_written.get(product, -1):
            return
        path = self._path(product)
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(HEADER)
            w.writerow([datetime.fromtimestamp(minute, timezone.utc).isoformat(),
                        o, h, l, c, round(vol, 6)])
        self.last_written[product] = minute

    def on_tick(self, product: str, wall: float, price: float, size: float = 0.0):
        minute = int(wall // 60) * 60
        bar = self.bar.get(product)
        if bar is None or bar[0] != minute:
            if bar is not None:
                self._flush(product)                       # previous minute is now complete
            self.bar[product] = [minute, price, price, price, price, size]
        else:
            bar[2] = max(bar[2], price)                     # high
            bar[3] = min(bar[3], price)                     # low
            bar[4] = price                                  # close
            bar[5] += size                                 # accumulate traded size

    def flush_all(self):
        for p in list(self.bar):
            self._flush(p)
