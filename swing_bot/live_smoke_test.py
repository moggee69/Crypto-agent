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
import getpass
import os
import sys

import broker


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    product = args[0] if args else "BTC-USD"
    usd = float(args[1]) if len(args) > 1 else 2.0

    if "--yes" not in flags:
        print(f"This will place a REAL market BUY of ~${usd:g} {product} and immediately SELL it back.")
        print(f"Re-run with --yes to confirm:\n    python live_smoke_test.py {product} {usd:g} --yes")
        sys.exit(0)

    # Keys: from the env if set, else the key id at a prompt (single line, safe to
    # paste) and the secret from a file (handles multi-line PEM keys reliably).
    key = os.environ.get("COINBASE_API_KEY")
    secret = os.environ.get("COINBASE_API_SECRET")
    secret_file = next((f.split("=", 1)[1] for f in flags if f.startswith("--secret-file=")), None)

    if not key:
        key = input("\n  API key id (paste the organizations/.../apiKeys/... line): ").strip()
    if not secret and secret_file:
        path = os.path.expanduser(secret_file)
        if not os.path.exists(path):
            sys.exit(f"ERROR: secret file not found: {path}")
        secret = open(path).read().strip()
        print(f"  (read secret from {path})")
    if not secret:
        print("\n  Paste your API secret. If it is a multi-line block starting with")
        print("  '-----BEGIN', press Ctrl+C and re-run with --secret-file=/root/cb_secret.txt")
        secret = getpass.getpass("  API secret (single-line, hidden): ").strip()
    if not (key and secret):
        sys.exit("ERROR: both the API key id and the secret are required.")
    os.environ["COINBASE_API_KEY"] = key
    os.environ["COINBASE_API_SECRET"] = secret
    if broker.kill_switch_engaged():
        sys.exit(f"ERROR: kill-switch file '{broker.KILL_SWITCH}' is present — remove it to run.")

    b = broker.Broker({"live": {"max_order_usd": max(usd * 2, 10)}})
    print(f"\n=== LIVE SMOKE TEST · {product} · ~${usd:g} ===")
    m = b.meta(product)
    print(f"product meta : base_inc={m['base_inc']}  quote_inc={m['quote_inc']}  "
          f"base_min={m['base_min']}  quote_min={m['quote_min']}")
    base, quote = (product.split("-") + ["USD"])[:2]
    print(f"{quote} available: {b.available(quote):,.2f}")

    print("\n[1/2] BUY ...")
    buy = b.market_buy(product, usd)
    print(f"  filled {buy.qty:g} @ ${buy.price:,.6g}   fee ${buy.fee:.4f}   "
          f"spent ${buy.quote_spent:.4f}   id {buy.order_id}")

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
