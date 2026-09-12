"""Write the account's free USDC balance to usdc_balance.json for the dashboard.

Read-only: calls get_accounts (View permission) and places NO orders. Loads the
bot's API key + secret-file path from the systemd env file itself, so it runs
standalone from cron. The dashboard's "USDC cash" tile reads the JSON it writes;
the fast 5-min page build never has to touch the Coinbase API.

    python report_usdc.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = "/etc/crypto-agent-003.env"          # COINBASE_API_KEY + COINBASE_API_SECRET_FILE
OUT = "/opt/crypto-agent/usdc_balance.json"


def load_env(path):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    load_env(ENV_FILE)
    sys.path.insert(0, HERE)
    import broker                                # imported after env is set
    b = broker.Broker({"live": {}})
    usdc = b.available("USDC")
    json.dump({"usdc": round(usdc, 2), "as_of": int(time.time())}, open(OUT, "w"))
    print(f"USDC ${usdc:.2f} -> {OUT}")


if __name__ == "__main__":
    main()
