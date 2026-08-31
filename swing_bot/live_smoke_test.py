"""Live smoke test — places ONE tiny REAL order and immediately sells it back, to
prove the whole execution path end-to-end BEFORE trusting a bot with real capital.

    python live_smoke_test.py [PRODUCT] [USD] --yes
    e.g.  python live_smoke_test.py BTC-USD 2 --yes

Requires COINBASE_API_KEY and COINBASE_API_SECRET in the environment (View+Trade).
Spends real money: the only cost of the test is the round-trip fee (a few cents on
a couple of dollars). Refuses to run without the explicit --yes flag.

What it proves: authentication, product-metadata lookup, order placement, reading
back the real fill (size/price/fee), and account balances — the exact path the
live bot uses.
"""
import os
import sys

import broker


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    product = args[0] if args else "BTC-USD"
    usd = float(args[1]) if len(args) > 1 else 2.0

    if not (os.environ.get("COINBASE_API_KEY") and os.environ.get("COINBASE_API_SECRET")):
        sys.exit("ERROR: set COINBASE_API_KEY and COINBASE_API_SECRET in the environment first.")
    if "--yes" not in flags:
        print(f"This will place a REAL market BUY of ~${usd:g} {product} and immediately SELL it back.")
        print(f"Re-run with --yes to confirm:\n    python live_smoke_test.py {product} {usd:g} --yes")
        sys.exit(0)
    if broker.kill_switch_engaged():
        sys.exit(f"ERROR: kill-switch file '{broker.KILL_SWITCH}' is present — remove it to run.")

    b = broker.Broker({"live": {"max_order_usd": max(usd * 2, 10)}})
    print(f"\n=== LIVE SMOKE TEST · {product} · ~${usd:g} ===")
    m = b.meta(product)
    print(f"product meta : base_inc={m['base_inc']}  quote_inc={m['quote_inc']}  "
          f"base_min={m['base_min']}  quote_min={m['quote_min']}")
    print(f"USD available: ${b.available('USD'):,.2f}")

    print("\n[1/2] BUY ...")
    buy = b.market_buy(product, usd)
    print(f"  filled {buy.qty:g} @ ${buy.price:,.6g}   fee ${buy.fee:.4f}   "
          f"spent ${buy.quote_spent:.4f}   id {buy.order_id}")

    base = product.split("-")[0]
    print(f"  {base} balance now: {b.available(base):g}")

    print("\n[2/2] SELL back ...")
    sell = b.market_sell(product, buy.qty)
    print(f"  filled {sell.qty:g} @ ${sell.price:,.6g}   fee ${sell.fee:.4f}   "
          f"received ${sell.quote_spent:.4f}   id {sell.order_id}")

    cost = buy.quote_spent - sell.quote_spent
    print(f"\n=== round-trip cost (fees + spread): ${cost:.4f} ===")
    print("SMOKE TEST PASSED — placement, fill read-back, balances and both sides all worked.")


if __name__ == "__main__":
    main()
