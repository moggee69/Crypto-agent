"""Daily momentum-rotation strategy.

Score = weighted blend of 24h / 7d / 30d returns.
Hold the top K coins with positive momentum, equal-weighted,
subject to per-position caps and a permanent USDC reserve.
If nothing has positive momentum, the bot sits 100% in USDC.
"""


def score_coins(coins: list[dict], weights: dict) -> list[dict]:
    for c in coins:
        c["momentum_score"] = (
            weights["pct_change_24h"] * c["pct_change_24h"]
            + weights["pct_change_7d"] * c["pct_change_7d"]
            + weights["pct_change_30d"] * c["pct_change_30d"]
        )
    return sorted(coins, key=lambda c: c["momentum_score"], reverse=True)


def build_target_portfolio(scored: list[dict], cfg: dict) -> dict[str, float]:
    """Return {symbol: target_usd} allocations."""
    risk = cfg["risk"]
    strat = cfg["strategy"]
    capital = risk["total_capital_usdc"]
    investable = capital * (1 - risk["cash_reserve_pct"] / 100)
    max_pos = capital * risk["max_position_pct"] / 100

    picks = [c for c in scored if c["momentum_score"] > strat["min_momentum_score"]]
    picks = picks[: cfg["universe"]["hold_top"]]

    if not picks:
        return {}  # all cash — momentum is negative across the board

    per_coin = min(investable / len(picks), max_pos)
    return {c["symbol"]: round(per_coin, 2) for c in picks}
