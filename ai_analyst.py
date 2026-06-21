"""Optional AI overlay: asks Claude to sanity-check each planned buy.

This is NOT a price predictor — no model can reliably predict prices.
It acts as a risk filter (e.g., flags coins with active exploit news,
regulatory action, or extreme volatility) and can veto a buy.
Requires ANTHROPIC_API_KEY in your environment.
"""
import json
import os

import anthropic  # pip install anthropic

PROMPT = """You are a risk analyst for a small automated crypto portfolio.
For the coin below, respond ONLY with JSON: {{"risk": "low"|"medium"|"high_risk", "reason": "<one sentence>"}}.
Flag high_risk only for serious red flags (exploits, delistings, regulatory action, death spirals).
Coin: {name} ({symbol}), 24h: {p24:.1f}%, 7d: {p7:.1f}%, 30d: {p30:.1f}%."""


def review_buy(coin: dict, model: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=model,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": PROMPT.format(
                name=coin["name"], symbol=coin["symbol"],
                p24=coin["pct_change_24h"], p7=coin["pct_change_7d"],
                p30=coin["pct_change_30d"],
            ),
        }],
    )
    try:
        return json.loads(msg.content[0].text.replace("```json", "").replace("```", "").strip())
    except (json.JSONDecodeError, IndexError):
        return {"risk": "medium", "reason": "Could not parse AI response"}
