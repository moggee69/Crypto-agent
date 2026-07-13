"""One-off admin command: buy EVERY watchlist coin now (equal split), then hand
the positions back to the bot's normal management (7-day-MA trend-break exit,
re-enter on the next dip). Paper only — overrides the usual dip-buy entry.

Run with the bot STOPPED so it doesn't overwrite state with its stale in-memory copy:
    systemctl stop swing-bot
    ./venv/bin/python buy_all.py
    systemctl start swing-bot
"""
import requests
import yaml

import data
from portfolio import Portfolio


def price_now(product: str) -> float:
    r = requests.get(f"https://api.exchange.coinbase.com/products/{product}/ticker", timeout=15)
    return float(r.json()["price"])


def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    pf = Portfolio(cfg)
    prices = {p: price_now(p) for p in cfg["watchlist"]}
    print("Buying every watchlist coin at market (equal split)...\n")
    for p in cfg["watchlist"]:
        st = pf.coin_state(p)
        if st["holding"]:
            print(f"  {p}: already holding — skipping")
            continue
        pf.buy(p, st, prices[p], "manual buy-all command")
        d = data.fetch_candles(p, 86400, 2)
        if d:
            st["last_daily_ts"] = d[-1]["t"]   # start exit checks from the NEXT daily close
    pf.save()
    print(f"\nDone. Equity now ${pf.equity(prices):,.2f} | "
          f"holding: {[p for p, s in pf.state['coins'].items() if s['holding']]}")


if __name__ == "__main__":
    main()
