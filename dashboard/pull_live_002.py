"""Pull Utility 002's LIVE state from the droplet into the dashboard data dir.

002 went LIVE (real USDC) on 2026-08-31, so its dashboard panel reads the real
running-bot files (portfolio + trades + equity), not the paper sim. Run this in
the refresh flow in place of the 002 half of sim_backfill.
"""
import os
import subprocess

import _paths

HOST = "root@165.227.84.219"
REMOTE = "/opt/crypto-agent/swing_bot"


def scp(remote_name, local_name):
    dst = os.path.join(_paths.DATA_DIR, local_name)
    subprocess.run(["scp", "-o", "ConnectTimeout=15",
                    f"{HOST}:{REMOTE}/{remote_name}", dst], check=True)
    return dst


scp("swing_portfolio.json", "sw_portfolio.json")   # real holdings + buy prices
scp("swing_trades.csv", "sw_trades.csv")           # real trade log (baseline seed + swings)
scp("swing_equity.csv", "sw_equity.csv")           # real equity curve since go-live
print("pulled LIVE 002 into sw_* (real money bot)")
