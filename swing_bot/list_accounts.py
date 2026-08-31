"""Read-only diagnostic — list the portfolios and account balances THIS API key
can actually see. Helps diagnose a $0 balance / "account is not available" error:
it shows whether the key reaches your cash, and which portfolio it's in.

    python list_accounts.py --secret-file=/root/cb_secret.txt

Places no orders. Uses the same key-loading as the smoke test.
"""
import getpass
import os
import sys

import broker


def main():
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    key = os.environ.get("COINBASE_API_KEY") or input("  API key id: ").strip()
    secret = os.environ.get("COINBASE_API_SECRET")
    sf = next((f.split("=", 1)[1] for f in flags if f.startswith("--secret-file=")), None)
    if not secret and sf:
        secret = open(os.path.expanduser(sf)).read().strip()
    if not secret:
        secret = getpass.getpass("  API secret (hidden): ").strip()
    os.environ["COINBASE_API_KEY"] = key
    os.environ["COINBASE_API_SECRET"] = secret

    b = broker.Broker({"live": {}})

    print("\n=== PORTFOLIOS this key can see ===")
    try:
        resp = b._retry(lambda: b.client.get_portfolios())
        ports = broker._field(resp, "portfolios") or []
        if not ports:
            print("  (none returned)")
        for p in ports:
            print(f"  name={broker._field(p, 'name')!r}  type={broker._field(p, 'type')}  "
                  f"uuid={broker._field(p, 'uuid')}  deleted={broker._field(p, 'deleted')}")
    except Exception as e:   # noqa: BLE001
        print(f"  (could not list portfolios: {e})")

    print("\n=== ACCOUNT BALANCES (USD + anything non-zero) ===")
    accts = b._retry(lambda: b.client.get_accounts())
    rows = broker._field(accts, "accounts") or []
    shown = 0
    for a in rows:
        cur = broker._field(a, "currency")
        bal = broker._field(a, "available_balance")
        avail = broker._num(broker._field(bal, "value") if bal is not None else None)
        hold = broker._field(a, "hold")
        held = broker._num(broker._field(hold, "value") if hold is not None else None)
        if cur == "USD" or avail > 0 or held > 0:
            print(f"  {cur:>6}  available={avail:<14g}  on_hold={held:g}")
            shown += 1
    if not shown:
        print("  (no balances — this key sees an empty account)")
    print(f"\nTotal accounts visible to this key: {len(rows)}")
    print("\nIf USD available is 0 but you added cash: the cash is either still")
    print("pending/settling, or sitting in a portfolio this key does not trade on.")


if __name__ == "__main__":
    main()
