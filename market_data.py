"""Fetch market data for the top N coins by market cap (CoinGecko, free, no key)."""
import requests

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"


def _looks_like_stablecoin(price: float, p7: float, p30: float) -> bool:
    """Heuristic stablecoin detector for coins not in the static exclude list.

    A coin pegged near $1 that barely moves over both a week and a month is a
    stablecoin (e.g. USDS, USD1) and has no place in a momentum strategy — its
    score hovers around zero and it can sneak in when real coins are scarce.
    The thresholds are deliberately tight so genuinely volatile coins that
    happen to trade near $1 are NOT misclassified.
    """
    if price is None:
        return False
    return 0.95 <= price <= 1.05 and abs(p7) < 2 and abs(p30) < 2


def get_top_coins(top_n: int, exclude: list[str]) -> list[dict]:
    """Return top coins by market cap with momentum fields."""
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        # Over-fetch generously so excluded coins + filtered stablecoins still
        # leave us with a full top_n.
        "per_page": top_n + len(exclude) + 20,
        "page": 1,
        "price_change_percentage": "24h,7d,30d",
    }
    resp = requests.get(COINGECKO_URL, params=params, timeout=30)
    resp.raise_for_status()

    coins = []
    for c in resp.json():
        symbol = c["symbol"].upper()
        if symbol in exclude:
            continue
        price = c["current_price"]
        p24 = c.get("price_change_percentage_24h_in_currency") or 0.0
        p7 = c.get("price_change_percentage_7d_in_currency") or 0.0
        p30 = c.get("price_change_percentage_30d_in_currency") or 0.0
        if _looks_like_stablecoin(price, p7, p30):
            continue  # skip pegged stablecoins (USDS, USD1, etc.)
        coins.append({
            "symbol": symbol,
            "name": c["name"],
            "price": price,
            "market_cap": c["market_cap"],
            "pct_change_24h": p24,
            "pct_change_7d": p7,
            "pct_change_30d": p30,
        })
        if len(coins) >= top_n:
            break
    return coins
