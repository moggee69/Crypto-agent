"""AI Crypto Trading Agent — daily run.

Usage:
    python main.py            # one daily cycle (respects dry_run in config.yaml)

Cycle:
 1. Fetch top-20 coins by market cap (CoinGecko)
 2. Score by blended momentum (24h / 7d / 30d)
 3. Build target portfolio (top 5, equal weight, caps + USDC reserve)
 4. Optional: Claude AI risk-vets each buy
 5. Diff vs current holdings -> sells first, then buys
"""
import sys
from datetime import datetime, timezone

import yaml

import market_data
import strategy
from executor import Executor


def _loss_limit_triggered(cur_equity, prev_equity, loss_limit_pct):
    """Return (halt: bool, change_pct: float) for the daily loss circuit breaker.

    Halts when equity has fallen by loss_limit_pct or more vs. ~24h ago.
    No-op when there's no reference yet (prev_equity is None) or limit is 0/unset.
    """
    if not loss_limit_pct or not prev_equity:
        return (False, 0.0)
    change_pct = (cur_equity - prev_equity) / prev_equity * 100
    return (change_pct <= -loss_limit_pct, change_pct)


def _is_trading_day(day_ordinal, every_n_days):
    """True on every Nth calendar day (every_n_days=1 means trade every day).

    Uses the day's ordinal so the alternation is continuous across month and
    year boundaries (no double/skip like a cron day-of-month rule would have).
    """
    if every_n_days <= 1:
        return True
    return day_ordinal % every_n_days == 0


def main():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    mode = "DRY RUN (no real orders)" if cfg["dry_run"] else "LIVE TRADING"
    quote = cfg["universe"]["quote_currency"]
    print(f"=== Crypto Agent | {mode} ===\n")

    # 1-2. Data + scoring
    coins = market_data.get_top_coins(
        cfg["universe"]["top_n_by_market_cap"], cfg["universe"]["exclude"]
    )
    scored = strategy.score_coins(coins, cfg["strategy"]["momentum_weights"])
    prices = {c["symbol"]: c["price"] for c in scored}

    print("Momentum leaderboard (top 10):")
    for c in scored[:10]:
        print(f"  {c['symbol']:<6} score {c['momentum_score']:>7.2f}  "
              f"(24h {c['pct_change_24h']:+.1f}% | 7d {c['pct_change_7d']:+.1f}% | 30d {c['pct_change_30d']:+.1f}%)")

    # 3. Targets
    targets = strategy.build_target_portfolio(scored, cfg)
    if not targets:
        print(f"\nNo coins with positive momentum -> holding 100% {quote} today.")
    else:
        print(f"\nTarget portfolio: " + ", ".join(f"{s} ${u:,.0f}" for s, u in targets.items()))

    # 4. Optional AI veto
    if cfg["ai_analyst"]["enabled"] and targets:
        from ai_analyst import review_buy
        by_symbol = {c["symbol"]: c for c in scored}
        for sym in list(targets):
            verdict = review_buy(by_symbol[sym], cfg["ai_analyst"]["model"])
            print(f"  AI check {sym}: {verdict['risk']} — {verdict['reason']}")
            if verdict["risk"] == cfg["ai_analyst"]["veto_threshold"]:
                print(f"  -> VETOED {sym}, staying in {quote} instead")
                del targets[sym]

    # 5. Rebalance: sells first, then buys
    ex = Executor(cfg)
    holdings = ex.get_holdings_usd(prices)
    min_trade = cfg["risk"]["min_trade_usd"]

    # Daily loss circuit breaker: if equity has dropped past the limit vs. ~24h
    # ago, halt ALL trading this run (hold everything) and force a review.
    loss_limit = cfg["risk"].get("daily_loss_limit_pct", 0)
    halted, change_pct = _loss_limit_triggered(
        ex.current_equity(prices), ex.reference_equity(24), loss_limit
    )

    # Trade only every Nth calendar day (off-days still record an equity snapshot).
    trade_every = cfg["strategy"].get("trade_every_n_days", 1)
    is_trading_day = _is_trading_day(
        datetime.now(timezone.utc).date().toordinal(), trade_every
    )

    print("\nRebalancing:")
    if halted:
        print(f"  *** DAILY LOSS LIMIT HIT: equity {change_pct:+.2f}% vs ~24h ago "
              f"(limit -{loss_limit}%).")
        print("  *** Trading halted - holding all positions. Review before next run.")
    elif not is_trading_day:
        print(f"  Skipped - not a scheduled trading day (rebalancing every "
              f"{trade_every} days). Holding positions.")
    else:
        traded = False
        for sym, held_usd in holdings.items():
            diff = targets.get(sym, 0) - held_usd
            if diff < -min_trade:
                price = ex.mark_price(sym, prices)
                if price is None:
                    print(f"  (skip {sym}: no current price available to sell)")
                    continue
                ex.market_sell(sym, abs(diff), price)
                traded = True
        for sym, target_usd in targets.items():
            diff = target_usd - holdings.get(sym, 0)
            if diff > min_trade:
                ex.market_buy(sym, diff, prices[sym])
                traded = True
        if not traded:
            print("  Portfolio already on target — no trades needed.")

    # Paper P&L summary (dry-run only; no-op when live)
    ex.report_paper_pnl(prices)

    print("\nDone. Full audit trail in trades_log.csv")


if __name__ == "__main__":
    sys.exit(main())
