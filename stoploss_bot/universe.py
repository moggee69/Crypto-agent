"""Dynamic trading universe for Agent 001 — pick the top-N "trending" coins.

Ranks Coinbase's own USD spot pairs (the only things the bot can actually trade)
by 24h price gain, keeping only pairs above a 24h-dollar-volume floor so the
thin flash-pumps are screened out before ranking. Falls back to the configured
watchlist on any error, so a data hiccup never leaves the bot with no universe.

Only called on a slow cadence (every couple of hours), never per tick.
"""
import concurrent.futures as cf

import requests

BASE = "https://api.exchange.coinbase.com"


def _usd_products() -> list[str]:
    r = requests.get(BASE + "/products", timeout=20)
    r.raise_for_status()
    out = []
    for p in r.json():
        if (p.get("quote_currency") == "USD" and p.get("status") == "online"
                and not p.get("trading_disabled") and not p.get("limit_only")
                and not p.get("cancel_only") and not p.get("post_only")
                and not p.get("auction_mode")):
            out.append(p["id"])
    return out


def _stat(pid: str) -> dict | None:
    try:
        r = requests.get(f"{BASE}/products/{pid}/stats", timeout=10)
        if r.status_code != 200:
            return None
        s = r.json()
        op, last = float(s.get("open") or 0), float(s.get("last") or 0)
        vol = float(s.get("volume") or 0)
        if op <= 0 or last <= 0:
            return None
        return {"id": pid, "gain": (last - op) / op * 100, "vol_usd": vol * last}
    except Exception:
        return None


def select_trending(cfg: dict, fallback: list[str]) -> list[dict]:
    """Return the top-N trending pairs as [{id, gain, vol_usd}], best first.
    On any failure returns the fallback watchlist (as dicts with gain/vol 0)."""
    u = cfg.get("universe", {})
    size = u.get("size", 10)
    min_vol = u.get("min_24h_volume_usd", 0)
    rank_by = u.get("rank_by", "gain_24h")
    fb = [{"id": p, "gain": 0.0, "vol_usd": 0.0} for p in fallback][:size]
    try:
        products = _usd_products()
    except Exception:
        return fb
    stats = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for s in ex.map(_stat, products):
            if s:
                stats.append(s)
    if not stats:
        return fb
    qualified = [s for s in stats if s["vol_usd"] >= min_vol] or stats
    key = "vol_usd" if rank_by == "volume_24h" else "gain"
    qualified.sort(key=lambda s: s[key], reverse=True)
    return qualified[:size]
