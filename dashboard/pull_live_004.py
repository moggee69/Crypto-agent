"""Pull Agent 004's (All-stars) LIVE state from the droplet into the dashboard data
dir, and synthesize its opening baseline trade log from the real holdings.

Why this exists: 004 started a "today" baseline with no deterministic backfill, so
regenerating it locally (agent004_baseline.py) pegs every buy_price to the CURRENT
close — which makes the Open-Positions P&L compute to ~0. The running bot on the
droplet holds the true 28-Aug entry prices, so 004's dashboard data must come from
the server. Run THIS in the refresh flow instead of agent004_baseline/_bundle.
"""
import subprocess, json, csv, os
import _paths

HOST = "root@165.227.84.219"
REMOTE = "/opt/crypto-agent/agent004"
FEE = 0.006


def scp(remote_name, local_name):
    dst = os.path.join(_paths.DATA_DIR, local_name)
    subprocess.run(["scp", "-o", "ConnectTimeout=15",
                    f"{HOST}:{REMOTE}/{remote_name}", dst], check=True)
    return dst


port_path = scp("swing_portfolio.json", "a4_portfolio.json")   # real holdings + buy prices
eq_path = scp("swing_equity.csv", "a4_equity.csv")             # real equity curve since launch

port = json.load(open(port_path))
rows = list(csv.DictReader(open(eq_path)))
entry_iso = rows[0]["timestamp_utc"] if rows else ""            # launch timestamp = baseline buy time

# synthesize the 8 opening baseline buys from the real holdings (server logs none —
# the baseline was seeded straight into the portfolio, not through the trade logger)
with open(os.path.join(_paths.DATA_DIR, "a4_trades.csv"), "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["timestamp_utc", "action", "product", "usd", "note"])
    for prod, c in port["coins"].items():
        cost = c.get("cost_usd", 0.0)
        w.writerow([entry_iso, "BUY", prod, round(cost, 2),
                    f"@ {c['buy_price']:.6g} baseline fee {cost * FEE:.2f}"])

cur = float(rows[-1]["equity"]) if rows else 500.0
pnl = float(rows[-1]["pnl_pct"]) if rows else 0.0
print(f"pulled 004 live state: {len(port['coins'])} holdings | ${cur:.2f} ({pnl:+.2f}%) | entry {entry_iso[:10]}")
