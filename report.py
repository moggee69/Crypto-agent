"""Paper-trading report — equity curve + summary stats.

Reads the dry-run track record and prints, in your terminal:
  - key stats (return, peak, max drawdown, fees, number of trades)
  - an ASCII equity curve over time

No extra dependencies (no matplotlib) — just the Python standard library.

Usage:
    python report.py
"""
import csv
import json
import os
import sys
from datetime import datetime

EQUITY_FILE = "equity_history.csv"
TRADES_FILE = "trades_log.csv"
STATE_FILE = "paper_portfolio.json"

CHART_WIDTH = 64   # columns of plot area
CHART_HEIGHT = 16  # rows of plot area


def load_equity() -> list[dict]:
    if not os.path.exists(EQUITY_FILE):
        return []
    with open(EQUITY_FILE, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["equity"] = float(r["equity"])
        r["pnl_pct"] = float(r["pnl_pct"])
        r["fees_paid"] = float(r["fees_paid"])
        r["ts"] = datetime.fromisoformat(r["timestamp_utc"])
    return rows


def starting_capital(rows: list[dict]) -> float:
    """Exact starting capital from the state file; fall back to back-calculation."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return float(json.load(f).get("start_capital", 0)) or _derive_start(rows)
    return _derive_start(rows)


def _derive_start(rows: list[dict]) -> float:
    r = rows[0]
    return r["equity"] - r["pnl_pct"] / 100 * r["equity"] if r["pnl_pct"] else r["equity"]


def count_trades() -> tuple[int, int]:
    """Return (buys, sells) recorded in the trade log."""
    if not os.path.exists(TRADES_FILE):
        return (0, 0)
    buys = sells = 0
    with open(TRADES_FILE, newline="") as f:
        for r in csv.DictReader(f):
            if r["action"] == "BUY":
                buys += 1
            elif r["action"] == "SELL":
                sells += 1
    return (buys, sells)


def max_drawdown(values: list[float]) -> float:
    """Largest peak-to-trough drop, as a percentage of the peak."""
    peak = values[0]
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, (v - peak) / peak * 100)
    return worst


def ascii_chart(rows: list[dict], start_cap: float) -> str:
    values = [r["equity"] for r in rows]
    # Always include the breakeven line in range so you can see up vs. down.
    lo, hi = min(values + [start_cap]), max(values + [start_cap])
    if hi == lo:  # flat line — give it a little vertical breathing room
        hi, lo = hi + 1, lo - 1

    # Build an empty grid, then plot one column per snapshot.
    grid = [[" "] * CHART_WIDTH for _ in range(CHART_HEIGHT)]
    n = len(values)

    # baseline (starting capital) as a dotted reference line first, so the
    # equity points draw on top of it
    base_level = int((start_cap - lo) / (hi - lo) * (CHART_HEIGHT - 1))
    base_row = CHART_HEIGHT - 1 - base_level
    for col in range(CHART_WIDTH):
        grid[base_row][col] = "."

    for col in range(CHART_WIDTH):
        # map this column back to a data point (handles n > or < width)
        v = values[min(int(col / CHART_WIDTH * n), n - 1)]
        level = int((v - lo) / (hi - lo) * (CHART_HEIGHT - 1))
        grid[CHART_HEIGHT - 1 - level][col] = "*"

    # Render with a left-hand $ axis (top, mid, bottom labels).
    out = []
    for i, line in enumerate(grid):
        if i == 0:
            label = f"${hi:>10,.2f}"
        elif i == CHART_HEIGHT - 1:
            label = f"${lo:>10,.2f}"
        elif i == CHART_HEIGHT // 2:
            label = f"${(hi + lo) / 2:>10,.2f}"
        else:
            label = " " * 11
        out.append(f"{label} |" + "".join(line))
    out.append(" " * 11 + " +" + "-" * CHART_WIDTH)
    out.append(" " * 13 + rows[0]["ts"].strftime("%Y-%m-%d")
               + " " * (CHART_WIDTH - 20) + rows[-1]["ts"].strftime("%Y-%m-%d"))
    return "\n".join(out)


def main():
    rows = load_equity()
    if not rows:
        print(f"No history yet — run 'python main.py' at least once to create "
              f"{EQUITY_FILE}.")
        return 1

    values = [r["equity"] for r in rows]
    first, last = rows[0], rows[-1]
    start_cap = starting_capital(rows)
    total_ret = last["pnl_pct"]
    buys, sells = count_trades()

    print("=" * 78)
    print("  PAPER TRADING REPORT")
    print("=" * 78)
    print(f"  Period:           {first['ts'].date()}  ->  {last['ts'].date()}"
          f"   ({len(rows)} run{'s' if len(rows) != 1 else ''})")
    print(f"  Starting capital: ${start_cap:,.2f}")
    print(f"  Current equity:   ${last['equity']:,.2f}")
    print(f"  Total return:     {total_ret:+.2f}%  (${last['equity'] - start_cap:+,.2f})")
    print(f"  Peak equity:      ${max(values):,.2f}")
    print(f"  Max drawdown:     {max_drawdown(values):.2f}%")
    print(f"  Simulated fees:   ${last['fees_paid']:,.2f}")
    print(f"  Trades logged:    {buys} buys, {sells} sells")
    print("=" * 78)
    print()
    print("  Equity curve  (* = equity, . = starting capital / breakeven)")
    print()
    print(ascii_chart(rows, start_cap))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
