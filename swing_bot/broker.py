"""Live order execution against the Coinbase Advanced Trade API.

Used ONLY when every live gate is open (see `is_live`). Everything in here places
REAL orders with REAL money, so it is deliberately conservative:

  * per-product minimums and increments are respected (or Coinbase rejects the order);
  * order size is capped by `live.max_order_usd`;
  * a kill-switch file halts all live orders instantly;
  * each order carries an idempotent `client_order_id`, so a retry after a network
    blip can never place a second order;
  * the ACTUAL fill (price, size, fee) is read back from the exchange — never the
    request estimate.

Market orders only in v1. The SDK (`coinbase-advanced-py`) is imported lazily, so
paper mode never needs it installed.
"""
import math
import os
import time
import uuid
from dataclasses import dataclass

# A kill-switch: if this file exists in the bot's working dir, NO live order is
# placed. Drop the file (`touch HALT`) to freeze live trading instantly.
KILL_SWITCH = os.environ.get("BOT_KILL_SWITCH", "HALT")


class BrokerError(Exception):
    """A live order could not be placed or confirmed. Callers skip the trade."""


@dataclass
class Fill:
    order_id: str
    qty: float          # base amount actually filled
    price: float        # average fill price
    fee: float          # total fees, USD
    quote_spent: float  # buys: gross + fee (USD out) · sells: gross - fee (USD in)


def is_live(cfg: dict) -> bool:
    """Real orders require ALL THREE gates open — otherwise the bot stays on paper.

      1. dry_run is false
      2. live.live_trading is true          (a separate, per-bot opt-in)
      3. API keys are present in the env
    Any one missing => paper. Absence of the `live` section => paper.
    """
    if cfg.get("dry_run", True):
        return False
    if not cfg.get("live", {}).get("live_trading", False):
        return False
    return bool(os.environ.get("COINBASE_API_KEY") and os.environ.get("COINBASE_API_SECRET"))


def kill_switch_engaged() -> bool:
    return os.path.exists(KILL_SWITCH)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _field(obj, *keys):
    """Read a field from an SDK response that may be a dict OR a typed object."""
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            return obj[k]
        if hasattr(obj, k):
            return getattr(obj, k)
    return None


class Broker:
    def __init__(self, cfg: dict):
        from coinbase.rest import RESTClient   # lazy: only needed for live trading
        self.client = RESTClient(api_key=os.environ["COINBASE_API_KEY"],
                                 api_secret=os.environ["COINBASE_API_SECRET"])
        live = cfg.get("live", {})
        self.max_order_usd = float(live.get("max_order_usd", 250))
        self.poll_secs = float(live.get("fill_poll_seconds", 2))
        self.poll_tries = int(live.get("fill_poll_tries", 30))
        self._meta: dict[str, dict] = {}

    # ---- product metadata (increments / minimums), cached ----
    def meta(self, product: str) -> dict:
        if product not in self._meta:
            p = self._retry(lambda: self.client.get_product(product))
            p = _field(p, "product") or p
            self._meta[product] = {
                "base_inc": _num(_field(p, "base_increment")) or 1e-8,
                "quote_inc": _num(_field(p, "quote_increment")) or 0.01,
                "base_min": _num(_field(p, "base_min_size")),
                "quote_min": _num(_field(p, "quote_min_size")),
            }
        return self._meta[product]

    @staticmethod
    def _floor_to(value: float, inc: float) -> float:
        if not inc or inc <= 0:
            return value
        return math.floor(value / inc) * inc

    @staticmethod
    def _fmt(value: float, inc: float) -> str:
        dp = max(0, -int(round(math.log10(inc)))) if inc and inc < 1 else 0
        return f"{value:.{dp}f}"

    def _retry(self, fn, attempts: int = 4):
        """Retry transient API failures. Order-placing calls pass the SAME
        client_order_id each attempt, so a retry can never double-order."""
        last = None
        for i in range(attempts):
            try:
                return fn()
            except Exception as e:   # noqa: BLE001 — network / rate-limit / transient
                last = e
                time.sleep(1.5 * (i + 1))
        raise BrokerError(f"API call failed after {attempts} attempts: {last}")

    # ---- orders ----
    def market_buy(self, product: str, quote_usd: float) -> Fill:
        if kill_switch_engaged():
            raise BrokerError("kill-switch engaged")
        m = self.meta(product)
        spend = min(quote_usd, self.max_order_usd)
        spend = self._floor_to(spend, m["quote_inc"])
        if m["quote_min"] and spend < m["quote_min"]:
            raise BrokerError(f"{product} buy ${spend:g} below min ${m['quote_min']:g}")
        if spend <= 0:
            raise BrokerError(f"{product} buy size rounded to zero")
        coid = uuid.uuid4().hex
        resp = self._retry(lambda: self.client.market_order_buy(
            client_order_id=coid, product_id=product,
            quote_size=self._fmt(spend, m["quote_inc"])))
        return self._settle(product, resp, is_buy=True)

    def market_sell(self, product: str, base_qty: float) -> Fill:
        if kill_switch_engaged():
            raise BrokerError("kill-switch engaged")
        m = self.meta(product)
        qty = self._floor_to(base_qty, m["base_inc"])
        if m["base_min"] and qty < m["base_min"]:
            raise BrokerError(f"{product} sell {qty:g} below min {m['base_min']:g}")
        if qty <= 0:
            raise BrokerError(f"{product} sell size rounded to zero")
        coid = uuid.uuid4().hex
        resp = self._retry(lambda: self.client.market_order_sell(
            client_order_id=coid, product_id=product,
            base_size=self._fmt(qty, m["base_inc"])))
        return self._settle(product, resp, is_buy=False)

    def _settle(self, product: str, resp, is_buy: bool) -> Fill:
        """Turn an order response into a confirmed Fill by polling the real order."""
        order_id = _field(resp, "order_id")
        if not order_id:
            sr = _field(resp, "success_response")
            order_id = _field(sr, "order_id") if sr else None
        if not order_id:
            err = _field(resp, "error_response") or resp
            raise BrokerError(f"{product} order not accepted: {err}")

        settled = None
        for _ in range(self.poll_tries):
            time.sleep(self.poll_secs)
            o = self._retry(lambda: self.client.get_order(order_id))
            order = _field(o, "order") or o
            status = _field(order, "status")
            if status in ("FILLED", "CANCELLED", "EXPIRED", "FAILED"):
                settled = order
                break
        if settled is None:
            raise BrokerError(f"{product} order {order_id} did not settle in time")

        status = _field(settled, "status")
        qty = _num(_field(settled, "filled_size"))
        price = _num(_field(settled, "average_filled_price"))
        fee = _num(_field(settled, "total_fees", "fee"))
        if qty <= 0 or price <= 0:
            raise BrokerError(f"{product} order {order_id} settled {status} with no fill")
        gross = qty * price
        return Fill(order_id=order_id, qty=qty, price=price, fee=fee,
                    quote_spent=(gross + fee if is_buy else gross - fee))

    # ---- account / reconciliation helpers ----
    def available(self, currency: str) -> float:
        """Available balance of a currency (e.g. 'XLM' or 'USD') on the account."""
        accts = self._retry(lambda: self.client.get_accounts())
        for a in (_field(accts, "accounts") or []):
            if _field(a, "currency") == currency:
                bal = _field(a, "available_balance")
                return _num(_field(bal, "value") if bal is not None else None) or _num(bal)
        return 0.0

    def last_fill_price(self, product: str) -> float:
        """Most recent fill price for a product — used to recover a cost basis
        after an unrecorded (mid-crash) fill. Best-effort; 0.0 if unavailable."""
        try:
            fills = self._retry(lambda: self.client.get_fills(product_id=product, limit=1))
            arr = _field(fills, "fills") or []
            if arr:
                return _num(_field(arr[0], "price"))
        except Exception:   # noqa: BLE001 — best effort only
            pass
        return 0.0
